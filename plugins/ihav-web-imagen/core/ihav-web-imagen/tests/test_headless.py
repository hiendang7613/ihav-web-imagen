import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/headless.py'
SPEC = importlib.util.spec_from_file_location('headless_images', SCRIPT)
headless = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(headless)


class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.receipt = headless.Receipt(Path(self.temporary.name) / 'run/receipt.json')
        self.receipt.create('test-run', 'Create one square blue vase.')
        self.editor = MagicMock()
        self.editor.fill = AsyncMock()
        self.editor.inner_text = AsyncMock(return_value='Create one square blue vase.')
        self.editor.evaluate = AsyncMock(side_effect=lambda *_: {'text': self.editor.inner_text.return_value, 'image_pill': False})
        self.scope = MagicMock()
        self.send = MagicMock()
        self.send.is_enabled = AsyncMock(return_value=True)
        self.send.get_attribute = AsyncMock(return_value='false')
        self.send.click = AsyncMock()
        self.page = MagicMock(url='https://chatgpt.com/c/owned-test')
        self.required = patch.object(headless, 'required', AsyncMock(return_value=self.send))
        self.required.start()
        self.addCleanup(self.required.stop)

    async def test_durable_intent_precedes_the_click(self):
        async def click(**_):
            saved = json.loads(self.receipt.path.read_text())
            self.assertEqual(saved['send_state'], 'intent')
            self.assertEqual(saved['send_clicks'], 1)
        self.send.click.side_effect = click
        await headless.submit_once(self.page, self.editor, self.scope, self.receipt)
        self.send.click.assert_awaited_once()
        self.assertEqual(self.receipt.path.stat().st_mode & 0o777, 0o600)

    async def test_uncertain_click_is_not_replayed(self):
        self.send.click.side_effect = TimeoutError('lost acknowledgement')
        with self.assertRaises(headless.ResearchError):
            await headless.submit_once(self.page, self.editor, self.scope, self.receipt)
        self.assertEqual(self.receipt.data['send_state'], 'unknown')
        self.assertEqual(self.receipt.data['conversation_url'], 'https://chatgpt.com/c/owned-test')
        with self.assertRaises(headless.ResearchError):
            await headless.submit_once(self.page, self.editor, self.scope, self.receipt)
        self.send.click.assert_awaited_once()
        self.editor.fill.assert_awaited_once()

    async def test_disabled_send_stays_before_intent(self):
        self.send.is_enabled.return_value = False
        with self.assertRaises(headless.ResearchError):
            await headless.submit_once(self.page, self.editor, self.scope, self.receipt)
        self.assertEqual(self.receipt.data['send_state'], 'not_sent')
        self.send.click.assert_not_awaited()

    async def test_editor_mismatch_does_not_send(self):
        self.editor.inner_text.return_value = 'An unrelated draft'
        with self.assertRaises(headless.ResearchError):
            await headless.submit_once(self.page, self.editor, self.scope, self.receipt)
        self.assertEqual(self.receipt.data['send_clicks'], 0)
        self.send.click.assert_not_awaited()

    async def test_wrong_turn_cannot_finish_this_run(self):
        self.receipt.update(send_state='intent')
        self.page.evaluate = AsyncMock(return_value={'matches':0,'user_count':1,'images':[{'key':'other','loaded':True}], 'generating':False,'alerts':[]})
        await headless.observe(self.page, self.receipt, wait_seconds=0)
        self.assertEqual(self.receipt.data['send_state'], 'unknown')
        self.assertEqual(self.receipt.data['generation_state'], 'not_started')
        self.assertFalse(self.receipt.data['library_verified'])

    async def test_visible_alert_ends_observation_without_resending(self):
        self.receipt.update(send_state='intent')
        self.page.evaluate = AsyncMock(return_value={'matches':0,'user_count':0,'images':[], 'generating':False,'alerts':['Image limit reached']})
        with patch.object(headless.asyncio,'sleep',AsyncMock()) as sleep:
            completed=await headless.observe(self.page,self.receipt,wait_seconds=600)
            sleep.assert_not_awaited()
        self.assertFalse(completed)
        self.assertEqual(self.receipt.data['send_state'],'unknown')
        self.assertEqual(self.receipt.data['reason'],'visible_chatgpt_alert')
        self.send.click.assert_not_awaited()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_closed_and_lock_released_after_start_fails(self):
        browser = MagicMock()
        browser.start = AsyncMock(side_effect=RuntimeError('startup failed'))
        browser.close = AsyncMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(headless,'load_config'), patch.object(headless,'Browser',return_value=browser):
            state = Path(directory)
            with self.assertRaises(RuntimeError):
                async with headless.session(state):
                    self.fail('must not yield')
            browser.close.assert_awaited_once()
            with headless.profile_lock(state):      # released: a second run can take it
                pass

    async def test_two_runs_cannot_share_the_cloakbrowser_profile(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(headless,'Browser') as browser:
            state = Path(directory)
            with headless.profile_lock(state):
                with self.assertRaises(headless.ResearchError) as error:
                    async with headless.session(state):
                        self.fail('must not yield')
            self.assertEqual(error.exception.code,'profile_busy')
            browser.assert_not_called()

    async def test_the_old_pause_flag_changes_nothing(self):
        browser = MagicMock(start=AsyncMock(), close=AsyncMock())
        with tempfile.TemporaryDirectory() as directory, patch.object(headless,'load_config'), patch.object(headless,'Browser',return_value=browser):
            async with headless.session(Path(directory),pause_idle_worker=True,accept_downloads=True) as opened:
                self.assertIs(opened, browser)
        browser.start.assert_awaited_once_with(headless=True, accept_downloads=True)


class ReceiptTests(unittest.TestCase):
    def test_repeated_generate_preserves_receipt_and_never_opens_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = headless.Receipt(root/'headless-images/test/receipt.json')
            receipt.create('test','Create a vase')
            receipt.update(send_state='confirmed',generation_state='completed',library_verified=True)
            original = receipt.path.read_bytes()
            args = argparse.Namespace(command='generate',state_dir=root,run_id='test',prompt_file=root/'absent')
            with patch.object(headless,'execute') as execute, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(headless.run_command(args),2)
                execute.assert_not_called()
            self.assertEqual(receipt.path.read_bytes(),original)

    def test_run_lock_rejects_second_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            with headless.run_lock(Path(directory)):
                with self.assertRaises(headless.ResearchError):
                    with headless.run_lock(Path(directory)):
                        self.fail('must not acquire another lock')

    def test_json_null_receipt_does_not_authorize_a_new_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            target=root/'headless-images/test/receipt.json'
            target.parent.mkdir(parents=True)
            target.write_text('null\n')
            prompt=root/'prompt.txt'
            prompt.write_text('Create one vase')
            args=argparse.Namespace(command='generate',state_dir=root,run_id='test',prompt_file=prompt)
            with patch.object(headless,'execute',AsyncMock(return_value={'library_verified':True})) as execute, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(headless.run_command(args),2)
                execute.assert_not_called()
            self.assertEqual(target.read_text(),'null\n')


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_fresh_observation_does_not_reuse_past_library_success(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt=headless.Receipt(Path(directory)/'receipt.json')
            receipt.create('recovery','Create a vase')
            receipt.update(send_state='confirmed',send_clicks=1,generation_state='completed',library_verified=True,library_verification_method='rendered_pixel_match',library_verified_at='earlier',library_card_names=['Vase.png'],conversation_url='https://chatgpt.com/c/owned')
            page=MagicMock()
            page.goto=AsyncMock()
            browser=MagicMock()
            browser.context.new_page=AsyncMock(return_value=page)
            browser.check_access=AsyncMock()
            @contextlib.asynccontextmanager
            async def local_session(*_,**__):
                yield browser
            args=argparse.Namespace(command='resume',state_dir=Path(directory),pause_idle_worker=False,wait_seconds=0,preview=False)
            with patch.object(headless,'session',local_session),patch.object(headless,'required',AsyncMock()),patch.object(headless,'observe',AsyncMock(return_value=False)),patch.object(headless,'verify_library',AsyncMock()) as verify,patch.object(headless,'submit_once',AsyncMock()) as submit:
                result=await headless.execute(args,receipt)
                verify.assert_not_awaited()
                submit.assert_not_awaited()
            self.assertFalse(result['library_verified'])
            self.assertEqual(receipt.data['last_library_verification']['card_names'],['Vase.png'])


class RenderedDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        binary = headless.load_config(headless.DEFAULT_STATE).browser_binary
        self.browser = headless.Browser(Path(self.temp.name), headless.load_config(headless.DEFAULT_STATE).model_copy(update={'browser_binary':binary}))
        await self.browser.start(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.context.new_page()

    async def test_new_composer_without_legacy_id_ignores_remove_tool_chip(self):
        fixture=Path(self.temp.name)/'composer.html'
        fixture.write_text('''<button aria-pressed="true">Chat</button><form><button aria-label="Remove Create image">Create image</button><div contenteditable="true" role="textbox" aria-label="Ask ChatGPT"></div><button aria-label="Send" disabled>Send</button></form>''')
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')
        editor, scope, draft = await headless.composer_state(self.page)
        self.assertEqual(draft,'')
        self.assertEqual(await editor.get_attribute('aria-label'),'Ask ChatGPT')
        self.assertEqual(await scope.locator(headless.SEND).count(),1)

    async def test_image_projection_is_bound_to_exact_posted_prompt(self):
        fixture=Path(self.temp.name)/'turn.html'
        fixture.write_text('''<div data-message-author-role="user">Another request</div><div data-message-author-role="assistant"><img id="old"></div><article data-turn-id="owned"><div data-message-author-role="user">Create one vase</div></article><div data-message-author-role="assistant"><img id="result"></div>''')
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')
        await self.page.evaluate('''() => {for(const id of ['old','result']) {const c=document.createElement('canvas');c.width=id==='old'?256:128;c.height=c.width;document.getElementById(id).src=c.toDataURL();}}''')
        await self.page.wait_for_function('Array.from(document.images).every(e=>e.complete&&e.naturalWidth>0)')
        view = await self.page.evaluate(headless.TURN_STATE, {'prompt':'Create one vase','stop':headless.STOP})
        self.assertEqual(view['matches'],1)
        self.assertEqual(view['turn_id'],'owned')
        self.assertEqual([[i['width'],i['height']] for i in view['images']], [[128,128]])

    async def test_long_collapsed_prompt_bubble_matches_exact_prompt_after_ui_suffix(self):
        prompt='Create one image from the supplied references. Preserve every instruction. ' + ('Keep all details exact. ' * 120)
        fixture=Path(self.temp.name)/'long-prompt.html'
        fixture.write_text(f'''<article data-turn-key="owned">
<div data-user-message-bubble><span>{prompt}</span><button type="button">…</button><button type="button">Show more</button></div>
<div class="composer-attachments"><img id="reference"></div>
<div data-testid="generated-image-gallery"><button data-testid="generated-image-preview"><img id="result"></button></div>
</article>''')
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')
        await self.page.evaluate('''() => {for(const id of ['reference','result']) {const c=document.createElement('canvas');c.width=128;c.height=128;document.getElementById(id).src=c.toDataURL();}}''')
        await self.page.wait_for_function('() => Array.from(document.images).every(e=>e.complete&&e.naturalWidth===128)')
        view=await self.page.evaluate(headless.TURN_STATE,{'prompt':prompt,'stop':headless.STOP})
        fingerprints=await self.page.evaluate(headless.TURN_FINGERPRINTS,{'prompt':prompt})
        mismatch=await self.page.evaluate(headless.TURN_STATE,{'prompt':prompt+' extra','stop':headless.STOP})
        self.assertEqual(view['matches'],1)
        self.assertEqual(view['turn_id'],'owned')
        self.assertEqual([[item['width'],item['height']] for item in view['images']],[[128,128]])
        self.assertEqual(len(fingerprints),1)
        self.assertEqual(mismatch['matches'],0)

    async def test_new_gallery_ignores_prior_turn_hidden_duplicate_and_toast(self):
        fixture=Path(self.temp.name)/'new-turn.html'
        fixture.write_text('''<div data-user-message-bubble hidden>Create one vase</div>
<div data-turn-key="old"><div data-user-message-bubble>Another request</div><div data-testid="generated-image-gallery"><button data-testid="generated-image-preview"><img id="old"></button></div></div>
<div data-turn-key="owned"><div data-user-message-bubble>Create one vase</div><div data-testid="generated-image-gallery"><button data-testid="generated-image-preview"><img id="result"></button></div></div>
<aside><img id="toast"></aside>''')
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')
        await self.page.evaluate('''() => {for(const id of ['old','result','toast']) {const c=document.createElement('canvas');c.width=id==='result'?128:256;c.height=c.width;document.getElementById(id).src=c.toDataURL();}}''')
        await self.page.wait_for_function('() => Array.from(document.images).every(e=>e.complete&&e.naturalWidth>0)')
        view = await self.page.evaluate(headless.TURN_STATE, {'prompt':'Create one vase','stop':headless.STOP})
        self.assertEqual(view['matches'],1)
        self.assertEqual(view['turn_id'],'owned')
        self.assertEqual(view['user_count'],2)
        self.assertEqual([[i['width'],i['height']] for i in view['images']], [[128,128]])

    async def test_pixel_identity_survives_distinct_blob_urls(self):
        fixture=Path(self.temp.name)/'pixels.html'
        fixture.write_text('''<div data-turn-key="owned"><div data-user-message-bubble>Create one vase</div><div data-testid="generated-image-gallery"><button data-testid="generated-image-preview"><img id="result" width="300" alt="Generated image 1"></button></div></div><img id="viewer" width="300" alt="Vase.png">''')
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')
        await self.page.evaluate('''async () => {const c=document.createElement('canvas');c.width=128;c.height=128;const x=c.getContext('2d');x.fillStyle='blue';x.fillRect(0,0,128,128);for(const id of ['result','viewer']) {const blob=await new Promise(resolve=>c.toBlob(resolve));document.getElementById(id).src=URL.createObjectURL(blob);}}''')
        await self.page.wait_for_function('() => Array.from(document.images).every(e=>e.complete&&e.naturalWidth>0)')
        owned=await self.page.evaluate(headless.TURN_FINGERPRINTS,{'prompt':'Create one vase'})
        viewer=await self.page.evaluate(headless.VIEWER_FINGERPRINTS,{'dimensions':[[128,128]]})
        self.assertEqual(len(owned),1)
        self.assertEqual(len(viewer),2)
        self.assertNotEqual(viewer[0]['key'],viewer[1]['key'])
        self.assertIsNotNone(owned[0]['pixel_sha256'])
        self.assertEqual(owned[0]['pixel_sha256'],viewer[1]['pixel_sha256'])
        scoped=await self.page.evaluate(headless.VIEWER_FINGERPRINTS,{'dimensions':[[128,128]],'cardName':'Vase.png'})
        self.assertEqual(len(scoped),1)
        self.assertEqual(scoped[0]['name'],'Vase.png')
        self.assertEqual(scoped[0]['pixel_sha256'],owned[0]['pixel_sha256'])

    async def test_resume_uses_real_locator_api_and_does_not_submit(self):
        fixture=Path(self.temp.name)/'resume.html'
        fixture.write_text('''<div data-turn-key="owned"><div data-user-message-bubble>Create one vase</div><div data-testid="generated-image-gallery"><button data-testid="generated-image-preview"><img id="result"></button></div></div>''')
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')
        await self.page.evaluate('''() => {const c=document.createElement('canvas');c.width=128;c.height=128;document.getElementById('result').src=c.toDataURL();}''')
        await self.page.wait_for_function('() => document.images[0].complete&&document.images[0].naturalWidth>0')
        receipt=headless.Receipt(Path(self.temp.name)/'run/receipt.json')
        receipt.create('resume-test','Create one vase')
        receipt.update(send_state='unknown',send_clicks=1,conversation_url='https://chatgpt.com/c/owned-test',generation_seconds=0.004)
        args=argparse.Namespace(command='resume',state_dir=headless.DEFAULT_STATE,pause_idle_worker=False,wait_seconds=0,preview=False)
        @contextlib.asynccontextmanager
        async def local_session(*_,**__):
            yield self.browser
        # Keep a real rendered locator surface, but make navigation and access
        # local-only. There is no ChatGPT request or authenticated test profile.
        with patch.object(headless,'session',local_session), patch.object(self.browser.context,'new_page',AsyncMock(return_value=self.page)), patch.object(self.page,'goto',AsyncMock()), patch.object(self.browser,'check_access',AsyncMock()), patch.object(headless,'verify_library',AsyncMock()), patch.object(headless,'submit_once',AsyncMock()) as submit:
            result=await headless.execute(args,receipt)
            submit.assert_not_awaited()
        self.assertEqual(result['send_clicks'],1)
        self.assertEqual(result['send_state'],'confirmed')
        self.assertEqual(result['generation_state'],'completed')
        self.assertIsNone(result['generation_seconds'])
        self.assertTrue(result['recovered'])

    async def test_effort_scope_records_instant_and_restores_previous_chip_on_error(self):
        fixture=Path(self.temp.name)/'effort.html'
        fixture.write_text('''<button id="chip" type="button" aria-label="Select ChatGPT model">Medium</button>
<div id="menu" role="menu" hidden><button role="menuitem" type="button">Instant</button>
<button role="menuitem" type="button">Medium, 2 of 5 power</button></div>
<script>
window.effortSelections=[];
const chip=document.getElementById('chip'),menu=document.getElementById('menu');
chip.onclick=()=>menu.hidden=!menu.hidden;
for(const option of menu.querySelectorAll('[role="menuitem"]')) option.onclick=()=>{
 window.effortSelections.push(option.textContent.split(',')[0]);chip.textContent=option.textContent.split(',')[0];menu.hidden=true;
};
</script>''')
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')
        receipt=headless.Receipt(Path(self.temp.name)/'effort-run/receipt.json')
        receipt.create('effort-run','Create one image')
        with self.assertRaisesRegex(RuntimeError,'offline sentinel'):
            async with headless.effort_scope(self.page,'instant',receipt):
                self.assertEqual(await self.page.locator(headless.EFFORT_CHIP).inner_text(),'Instant')
                self.assertEqual(receipt.data['effort']['state'],'applied')
                raise RuntimeError('offline sentinel')
        self.assertEqual(await self.page.locator(headless.EFFORT_CHIP).inner_text(),'Medium')
        self.assertEqual(await self.page.evaluate('window.effortSelections'),['Instant','Medium'])
        self.assertEqual(receipt.data['effort']['previous'],'Medium')
        self.assertEqual(receipt.data['effort']['requested'],'instant')
        self.assertEqual(receipt.data['effort']['observed_after_selection'],'Instant')
        self.assertEqual(receipt.data['effort']['observed_after_restore'],'Medium')
        self.assertEqual(receipt.data['effort']['state'],'restored')
        self.assertEqual(receipt.data['effort']['restore_state'],'verified')
        self.assertGreaterEqual(receipt.data['effort']['apply_seconds'],0)
        self.assertGreaterEqual(receipt.data['effort']['restore_seconds'],0)
        self.assertEqual(receipt.summary()['effort']['state'],'restored')

    async def test_effort_scope_selects_medium_and_restores_instant(self):
        fixture=Path(self.temp.name)/'effort-medium.html'
        fixture.write_text('''<button id="chip" type="button" aria-label="Select ChatGPT model">Instant</button>
<div id="menu" role="menu" hidden><button role="menuitem" type="button">Instant</button>
<button role="menuitem" type="button">Medium, 2 of 5 power</button></div>
<script>
window.effortSelections=[];
const chip=document.getElementById('chip'),menu=document.getElementById('menu');
chip.onclick=()=>menu.hidden=!menu.hidden;
for(const option of menu.querySelectorAll('[role="menuitem"]')) option.onclick=()=>{
 window.effortSelections.push(option.textContent.split(',')[0]);chip.textContent=option.textContent.split(',')[0];menu.hidden=true;
};
</script>''')
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')
        receipt=headless.Receipt(Path(self.temp.name)/'effort-medium-run/receipt.json')
        receipt.create('effort-medium-run','Create one image')
        async with headless.effort_scope(self.page,'medium',receipt):
            self.assertEqual(await self.page.locator(headless.EFFORT_CHIP).inner_text(),'Medium')
            self.assertEqual(receipt.data['effort']['state'],'applied')
            self.assertEqual(receipt.data['effort']['observed_after_selection'],'Medium')
        self.assertEqual(await self.page.locator(headless.EFFORT_CHIP).inner_text(),'Instant')
        self.assertEqual(await self.page.evaluate('window.effortSelections'),['Medium','Instant'])
        self.assertEqual(receipt.data['effort']['previous'],'Instant')
        self.assertEqual(receipt.data['effort']['requested'],'medium')
        self.assertEqual(receipt.data['effort']['observed_after_restore'],'Instant')
        self.assertEqual(receipt.data['effort']['state'],'restored')
        self.assertEqual(receipt.data['effort']['restore_state'],'verified')

    async def test_effort_scope_fails_closed_when_instant_option_is_absent(self):
        fixture=Path(self.temp.name)/'effort-unavailable.html'
        fixture.write_text('''<button id="chip" type="button" aria-label="Select ChatGPT model">Medium</button>
<div id="menu" role="menu" hidden><button role="menuitem" type="button">Medium, 2 of 5 power</button></div>
<script>document.getElementById('chip').onclick=()=>document.getElementById('menu').hidden=false;</script>''')
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')
        with self.assertRaises(headless.ResearchError) as error:
            async with headless.effort_scope(self.page,'instant'):
                self.fail('must not enter without a unique Instant option')
        self.assertEqual(error.exception.code,'effort_option_unverified')
        self.assertEqual(await self.page.locator(headless.EFFORT_CHIP).inner_text(),'Medium')


class ImagePillTests(unittest.IsolatedAsyncioTestCase):
    """2026-10-04 UI: the Create image tool is a pill inside the editor (data-system-hint-type="picture_v2")."""

    async def test_the_prompt_is_typed_after_the_pill_and_never_replaces_it(self):
        receipt = headless.Receipt(Path(tempfile.mkdtemp()) / 'run/receipt.json')
        receipt.create('pill-run', 'a red fox')
        state = {'text': '', 'image_pill': True}
        editor = MagicMock(fill=AsyncMock(side_effect=AssertionError('fill would delete the pill')), click=AsyncMock(),
                           evaluate=AsyncMock(side_effect=lambda *_: dict(state)))
        page = MagicMock(url='https://chatgpt.com/')
        page.keyboard.press = AsyncMock()
        page.keyboard.insert_text = AsyncMock(side_effect=lambda text: state.update(text=text))
        send = MagicMock(is_enabled=AsyncMock(return_value=True), get_attribute=AsyncMock(return_value='false'), click=AsyncMock())
        with patch.object(headless, 'required', AsyncMock(return_value=send)):
            await headless.submit_once(page, editor, MagicMock(), receipt)
        page.keyboard.insert_text.assert_awaited_once_with('a red fox')
        send.click.assert_awaited_once()

    async def test_losing_the_pill_while_typing_stops_before_the_intent(self):
        receipt = headless.Receipt(Path(tempfile.mkdtemp()) / 'run/receipt.json')
        receipt.create('pill-run', 'a red fox')
        answers = iter([{'text': '', 'image_pill': True}, {'text': 'a red fox', 'image_pill': False}])
        editor = MagicMock(click=AsyncMock(), evaluate=AsyncMock(side_effect=lambda *_: next(answers)))
        page = MagicMock(url='https://chatgpt.com/')
        page.keyboard.press = AsyncMock()
        page.keyboard.insert_text = AsyncMock()
        with self.assertRaises(headless.ResearchError) as error:
            await headless.submit_once(page, editor, MagicMock(), receipt)
        self.assertEqual(error.exception.code, 'image_mode_unverified')
        self.assertEqual((receipt.data['send_state'], receipt.data.get('send_clicks', 0)), ('not_sent', 0))


if __name__ == '__main__':
    unittest.main()
