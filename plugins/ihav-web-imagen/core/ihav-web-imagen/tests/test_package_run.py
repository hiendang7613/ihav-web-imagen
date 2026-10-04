import asyncio
import base64
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import package_run
import image_files
from package_run import headless
from headless import ResearchError

PIXEL = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')


def make_package(root, name='case', *, order=('1_source.png (source, first)', '2_guide.png (canvas, second)'), prompt='Edit the canvas.'):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / 'prompt.txt').write_text(prompt + '\n')
    (folder / '1_source.png').write_bytes(PIXEL)
    (folder / '2_guide.png').write_bytes(PIXEL + b'guide')
    (folder / 'package.json').write_text(json.dumps({'order': list(order)}))
    return folder


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_order_comes_from_package_json_and_annotations_are_dropped(self):
        prompt, paths = package_run.load_package(make_package(self.root))
        self.assertEqual(prompt, 'Edit the canvas.')
        self.assertEqual([p.name for p in paths], ['1_source.png', '2_guide.png'])

    def test_extra_files_of_the_folder_are_never_picked_up(self):
        folder = make_package(self.root, order=('2_guide.png', '1_source.png'))
        (folder / '3_output.png').write_bytes(PIXEL)
        _, paths = package_run.load_package(folder)
        self.assertEqual([p.name for p in paths], ['2_guide.png', '1_source.png'])

    def test_missing_order_or_prompt_is_rejected(self):
        folder = make_package(self.root)
        (folder / 'package.json').write_text('{}')
        with self.assertRaises(headless.ResearchError) as error:
            package_run.load_package(folder)
        self.assertEqual(error.exception.code, 'package_invalid')
        other = make_package(self.root, 'other')
        (other / 'prompt.txt').write_text('   ')
        with self.assertRaises(headless.ResearchError) as error:
            package_run.load_package(other)
        self.assertEqual(error.exception.code, 'invalid_prompt')

    def test_reference_outside_the_folder_or_with_a_wrong_type_is_rejected(self):
        for bad in ('../escape.png', 'missing.png', 'prompt.txt'):
            folder = make_package(self.root, 'bad' + str(abs(hash(bad)) % 1000), order=(bad,))
            (self.root / 'escape.png').write_bytes(PIXEL)
            with self.assertRaises(headless.ResearchError, msg=bad):
                package_run.load_package(folder)

    def test_more_than_two_references_are_rejected(self):
        folder = make_package(self.root, order=('1_source.png', '2_guide.png', '1_source.png'))
        with self.assertRaises(headless.ResearchError):
            package_run.load_package(folder)

    def test_create_runs_writes_unsent_receipts_with_unique_ids_before_any_browser(self):
        first = make_package(self.root, 'same')
        state, out = self.root / 'state', self.root / 'out'
        receipts = package_run.create_runs(state, [first, first], out)
        self.assertEqual(len({r.data['run_id'] for r in receipts}), 2)
        for receipt in receipts:
            self.assertEqual(receipt.data['send_state'], 'not_sent')
            self.assertEqual(receipt.data['send_clicks'], 0)
            self.assertEqual([r['filename'] for r in receipt.data['references']], ['1_source.png', '2_guide.png'])
            self.assertEqual(Path(receipt.data['download_stem']).parent, out.resolve())
            self.assertEqual(receipt.data['required_reference_count'],2)
            self.assertEqual(receipt.data['reference_state'],'ready')

    def test_existing_output_blocks_before_a_receipt_or_browser(self):
        package = make_package(self.root)
        out = self.root / 'out'
        out.mkdir()
        with patch.object(package_run, 'run_id_for', return_value='fixed'):
            (out / 'fixed.png').write_bytes(b'keep')
            with self.assertRaises(headless.ResearchError) as error:
                package_run.create_runs(self.root / 'state', [package], out)
        self.assertEqual(error.exception.code, 'download_target_exists')
        self.assertFalse((self.root / 'state').exists())
        self.assertEqual((out / 'fixed.png').read_bytes(), b'keep')

    def test_cli_reports_a_bad_package_without_starting_the_browser(self):
        with patch.object(package_run, 'run_all', new=AsyncMock()) as run_all, contextlib.redirect_stdout(io.StringIO()) as out:
            code = package_run.main(['--package', str(self.root / 'nope'), '--download-dir', str(self.root / 'o'),
                                     '--state-dir', str(self.root / 's')])
        self.assertEqual(code, 2)
        run_all.assert_not_called()
        self.assertIn('error', json.loads(out.getvalue()))

    def test_concurrency_and_wait_are_bounded(self):
        folder = make_package(self.root)
        for extra in (['--concurrency', '0'], ['--concurrency', '5'], ['--wait-seconds', '0']):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                package_run.main(['--package', str(folder), '--download-dir', str(self.root / 'o'), *extra])


class PhaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.receipt = headless.Receipt(Path(self.temporary.name) / 'run/receipt.json')
        self.receipt.create('phase-run', 'Create one image')

    def test_mark_stamps_a_phase_once_in_order(self):
        headless.mark(self.receipt, 'start')
        headless.mark(self.receipt, 'intent')
        headless.mark(self.receipt, 'intent')
        self.assertEqual(list(self.receipt.data['phases']), ['start', 'intent'])
        self.assertEqual(self.receipt.data['phase_seconds']['start'], 0)
        self.assertGreaterEqual(self.receipt.data['phase_seconds']['intent'], 0)
        self.assertIn('phase_seconds', self.receipt.summary())

    def page_with(self, views, image):
        views = iter(views)

        async def evaluate(script, *_):
            if script is headless.TURN_STATE:
                return next(views)
            return [{**image, 'pixel_sha256': 'a' * 64}]
        return MagicMock(url='https://chatgpt.com/c/owned', evaluate=evaluate)

    def view(self, *images, generating=False, preview=False):
        return {'matches': 1, 'user_count': 1, 'turn_id': 't1', 'images': list(images), 'generating': generating,
                'preview': preview, 'alerts': []}

    async def observe(self, views, image):
        sleeps = []

        async def sleep(seconds):
            sleeps.append(seconds)
        with patch.object(headless.asyncio, 'sleep', sleep), contextlib.redirect_stdout(io.StringIO()):
            done = await headless.observe(self.page_with(views, image), self.receipt, wait_seconds=600, generation_started=0)
        return done, sleeps

    async def test_observe_polls_at_the_fast_cadence_and_stamps_each_phase(self):
        self.receipt.update(send_state='intent')
        image = {'key': 'f1', 'file_id': 'f1', 'width': 3546, 'height': 443, 'loaded': True}
        done, sleeps = await self.observe([
            self.view(generating=True), self.view(image, generating=True, preview=True),
            self.view(image), self.view(image)], image)
        self.assertTrue(done)
        self.assertEqual(sleeps, [headless.POLL_SECONDS] * 3)
        self.assertEqual(list(self.receipt.data['phases']), ['turn_posted', 'preview_visible', 'final_complete'])
        self.assertEqual(self.receipt.data['image_dimensions'], [[3546, 443]])

    async def test_preview_badge_or_stop_control_alone_keeps_the_run_pending(self):
        self.receipt.update(send_state='intent')
        image = {'key': 'f1', 'file_id': 'f1', 'width': 3546, 'height': 443, 'loaded': True}
        for pending in (self.view(image, preview=True), self.view(image, generating=True)):
            receipt = headless.Receipt(Path(self.temporary.name) / f'p{id(pending)}/receipt.json')
            receipt.create('pending-run', 'Create one image')
            receipt.update(send_state='intent', observation_deadline=(datetime.now(timezone.utc) + timedelta(seconds=0.05)).isoformat())
            page = self.page_with([pending] * 50, image)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(await headless.observe(page, receipt, wait_seconds=0.05, generation_started=0))
            self.assertEqual(receipt.data['generation_state'], 'pending')
            self.assertNotIn('final_complete', receipt.data['phases'])
            self.assertIn('preview_visible', receipt.data['phases'])

    async def test_a_changing_size_is_not_final_until_it_reads_the_same_twice(self):
        self.receipt.update(send_state='intent')
        small = {'key': 'f1', 'file_id': 'f1', 'width': 1772, 'height': 221, 'loaded': True}
        final = {**small, 'width': 3546, 'height': 443}
        done, sleeps = await self.observe([self.view(small), self.view(final), self.view(final)], final)
        self.assertTrue(done)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(self.receipt.data['image_dimensions'], [[3546, 443]])

    async def test_multiline_prompt_is_compared_by_its_words(self):
        prompt = 'HARD RULES\n   1. Keep the bars\n      same thickness.'
        receipt = headless.Receipt(Path(self.temporary.name) / 'multi/receipt.json')
        receipt.create('multi-run', prompt)
        editor = MagicMock(fill=AsyncMock(), inner_text=AsyncMock(return_value='HARD RULES\n\n1. Keep the bars\n\nsame thickness.'))
        editor.evaluate = AsyncMock(side_effect=lambda *_: {'text': editor.inner_text.return_value, 'image_pill': False})
        send = MagicMock(is_enabled=AsyncMock(return_value=True), get_attribute=AsyncMock(return_value='false'), click=AsyncMock())
        with patch.object(headless, 'required', AsyncMock(return_value=send)):
            await headless.submit_once(MagicMock(url='https://chatgpt.com/'), editor, MagicMock(), receipt)
        send.click.assert_awaited_once()
        self.assertEqual(list(receipt.data['phases']), ['prompt_inserted', 'intent'])
        editor.inner_text.return_value = 'HARD RULES 1. Keep the bars same thickness'
        other = headless.Receipt(Path(self.temporary.name) / 'other/receipt.json')
        other.create('other-run', prompt)
        with patch.object(headless, 'required', AsyncMock(return_value=send)), self.assertRaises(headless.ResearchError):
            await headless.submit_once(MagicMock(url='https://chatgpt.com/'), editor, MagicMock(), other)
        self.assertEqual(other.data['send_clicks'], 0)


class PrepareTests(unittest.IsolatedAsyncioTestCase):
    def fakes(self):
        other = MagicMock()
        other.is_closed.return_value = False
        other.locator.return_value.count = AsyncMock(return_value=1)
        counts = iter([0, 0, 1, 1, 1, 1])
        pages = []
        library = MagicMock()
        library.is_closed.return_value = False
        library.locator.return_value.count = AsyncMock(side_effect=lambda: next(counts))
        library.locator.return_value.evaluate_all = AsyncMock(return_value=[])
        library.get_by_role.return_value.click = AsyncMock(side_effect=lambda: pages.append(other))
        browser = MagicMock(check_access=AsyncMock())
        browser.context.pages = pages
        return browser, library, other

    async def prepare(self, popups):
        browser, library, other = self.fakes()
        with patch.object(headless, 'library_page', AsyncMock(return_value=library)), \
                patch.object(headless, 'composer_state', AsyncMock(return_value=('editor', 'scope', ''))), \
                patch.object(headless, 'select_image_tool', AsyncMock()), \
                patch.object(headless.asyncio, 'sleep', AsyncMock()):
            page, *_ = await headless.prepare(browser, popups=popups)
        return page, library, other

    async def test_single_run_may_adopt_a_popup_composer(self):
        page, _, other = await self.prepare(True)
        self.assertIs(page, other)

    async def test_concurrent_run_never_adopts_another_runs_composer(self):
        page, library, _ = await self.prepare(False)
        self.assertIs(page, library)


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def receipts(self, count=1):
        package = make_package(self.root)
        return package_run.create_runs(self.root / 'state', [package] * count, self.root / 'out')

    def patches(self, *, completed=True, submit=None):
        page = MagicMock(url='https://chatgpt.com/c/owned')
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(headless, 'prepare', AsyncMock(return_value=(page, MagicMock(), MagicMock(), []))))
        stack.enter_context(patch.object(package_run, 'upload_references', AsyncMock(return_value=[])))
        self.submit = stack.enter_context(patch.object(headless, 'submit_once', submit or AsyncMock()))
        self.observe = stack.enter_context(patch.object(headless, 'observe', AsyncMock(return_value=completed)))
        self.download = stack.enter_context(patch.object(package_run, 'download_from_turn', AsyncMock()))
        self.verify = stack.enter_context(patch.object(headless, 'verify_library', AsyncMock()))
        self.addCleanup(stack.close)

    async def run_one(self, receipt, stop=None, verify=False, slots=None):
        return await package_run.run_one(MagicMock(), receipt, wait_seconds=60, verify_library=verify,
                                         slots=slots or asyncio.Semaphore(2), stop=stop or asyncio.Event())

    async def test_library_is_not_verified_by_default_and_phases_are_stamped(self):
        (receipt,) = self.receipts()
        self.patches()
        summary = await self.run_one(receipt)
        self.submit.assert_awaited_once()
        self.download.assert_awaited_once()
        self.verify.assert_not_awaited()
        self.assertEqual(list(summary['phases']), ['start', 'composer_ready', 'upload_ready', 'downloaded'])

    async def test_library_check_is_optional_and_stamped_only_when_seen(self):
        (receipt,) = self.receipts()
        self.patches()
        self.verify.side_effect = lambda *_: receipt.update(library_verified=True)
        summary = await self.run_one(receipt, verify=True)
        self.verify.assert_awaited_once()
        self.assertIn('library_verified', summary['phases'])

    async def test_stopped_batch_does_not_send_the_remaining_package(self):
        (receipt,) = self.receipts()
        self.patches()
        stop = asyncio.Event()
        stop.set()
        summary = await self.run_one(receipt, stop)
        self.submit.assert_not_awaited()
        self.assertEqual(summary['send_state'], 'not_sent')
        self.assertEqual(receipt.data['reason'], 'batch_stopped_before_send')

    async def test_visible_alert_stops_the_rest_and_keeps_the_evidence(self):
        first, second = self.receipts(2)
        self.patches(completed=False)
        self.observe.side_effect = lambda page, receipt, **_: receipt.update(send_state='unknown', reason='visible_chatgpt_alert') or False
        stop = asyncio.Event()
        slots = asyncio.Semaphore(1)
        results = await asyncio.gather(self.run_one(first, stop, slots=slots), self.run_one(second, stop, slots=slots))
        self.assertTrue(stop.is_set())
        self.assertEqual(results[0]['reason'], 'visible_chatgpt_alert')
        self.assertEqual(results[0]['send_state'], 'unknown')
        self.assertEqual(results[1]['send_state'], 'not_sent')
        self.assertEqual(self.submit.await_count, 1)

    async def two_in_sequence(self, *, submit=None, observe=None):
        first, second = self.receipts(2)
        self.patches(completed=False, submit=submit)
        if observe:
            self.observe.side_effect = observe
        stop = asyncio.Event()
        slots = asyncio.Semaphore(1)
        results = await asyncio.gather(self.run_one(first, stop, slots=slots), self.run_one(second, stop, slots=slots))
        return stop, results, (first, second)

    async def test_unverified_turn_stops_the_queued_packages_and_keeps_the_sent_receipt(self):
        async def sent(page, editor, scope, receipt, **_):          # a real Send: durable intent and one click
            receipt.update(send_state='intent', send_clicks=1)
        stop, results, (first, second) = await self.two_in_sequence(
            submit=AsyncMock(side_effect=sent),
            observe=lambda page, receipt, **_: receipt.update(send_state='unknown', reason='posted_prompt_unverified') or False)
        self.assertTrue(stop.is_set())
        self.assertEqual((results[0]['send_state'], results[0]['reason'], results[0]['send_clicks']), ('unknown', 'posted_prompt_unverified', 1))
        self.assertEqual((results[1]['send_state'], second.data['reason'], results[1]['send_clicks']), ('not_sent', 'batch_stopped_before_send', 0))
        self.assertEqual(self.submit.await_count, 1)

    async def test_failure_after_intent_stops_the_queued_packages(self):
        async def click_lost(page, editor, scope, receipt, **_):
            receipt.update(send_state='unknown', reason='send_click_uncertain', send_clicks=1)
            raise ResearchError('send_click_uncertain', 'Reconcile this run')

        stop, results, (first, second) = await self.two_in_sequence(submit=click_lost)
        self.assertTrue(stop.is_set())
        self.assertEqual((results[0]['send_state'], results[0]['reason'], results[0]['send_clicks']), ('unknown', 'send_click_uncertain', 1))
        self.assertEqual((results[1]['send_state'], second.data['reason']), ('not_sent', 'batch_stopped_before_send'))

    async def test_pre_send_failure_does_not_stop_the_batch(self):
        stop, results, (first, second) = await self.two_in_sequence(
            submit=AsyncMock(side_effect=[ResearchError('send_unavailable', 'covered'), None]),
            observe=lambda page, receipt, **_: True)
        self.assertFalse(stop.is_set())
        self.assertEqual((results[0]['send_state'], results[0]['reason']), ('not_sent', 'send_unavailable'))
        self.assertEqual(self.submit.await_count, 2)

    async def test_slow_generation_with_a_confirmed_turn_does_not_stop_the_batch(self):
        stop, results, (first, second) = await self.two_in_sequence(
            observe=lambda page, receipt, **_: receipt.update(send_state='confirmed', generation_state='pending') or False)
        self.assertFalse(stop.is_set())
        self.assertEqual(self.submit.await_count, 2)
        self.assertEqual([r['send_state'] for r in results], ['confirmed', 'confirmed'])

    async def test_concurrency_is_bounded_by_the_slots(self):
        receipts = self.receipts(4)
        active = peak = 0

        async def submit(*_, **__):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
        self.patches(submit=submit)
        slots = asyncio.Semaphore(2)
        await asyncio.gather(*[self.run_one(r, slots=slots) for r in receipts])
        self.assertEqual(peak, 2)

    async def test_failure_after_intent_becomes_unknown_and_is_never_replayed(self):
        (receipt,) = self.receipts()
        self.patches()
        self.observe.side_effect = RuntimeError('lost browser')
        receipt.update(send_state='intent')
        summary = await self.run_one(receipt)
        self.assertEqual(summary['send_state'], 'unknown')
        self.assertEqual(summary['reason'], 'RuntimeError')
        self.assertEqual(summary['conversation_url'], 'https://chatgpt.com/c/owned')
        self.submit.assert_awaited_once()

    async def test_login_problem_stops_the_batch(self):
        (receipt,) = self.receipts()
        self.patches()
        headless.prepare.side_effect = headless.ResearchError('login_required', 'sign in')
        stop = asyncio.Event()
        summary = await self.run_one(receipt, stop)
        self.assertTrue(stop.is_set())
        self.assertEqual(summary['reason'], 'login_required')
        self.submit.assert_not_awaited()


class EffortTests(unittest.IsolatedAsyncioTestCase):
    """package_run --effort: the profile-wide chip is changed around ONE package's whole flow and restored."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def receipts(self, count=1):
        return package_run.create_runs(self.root / 'state', [make_package(self.root)] * count, self.root / 'out')

    def scope(self, log, *, restore_fails=False):
        @contextlib.asynccontextmanager
        async def effort_scope(page, requested, receipt=None):
            log.append(('enter', requested))
            try:
                yield
            finally:
                log.append(('exit', requested))
                if restore_fails:
                    receipt.update(effort={'restore_state': 'failed', 'state': 'restore_failed'})
                    raise ResearchError('effort_restore_failed', 'The original setting could not be restored')
        return effort_scope

    def patched(self, log, *, restore_fails=False):
        page = MagicMock(url='https://chatgpt.com/c/owned')
        async def record(name, *_, **__):
            log.append(name)
            return [] if name == 'upload' else None
        async def submit(page, editor, scope, receipt, **_):
            log.append('submit')
            receipt.update(send_state='intent', send_clicks=1)

        async def observe(page, receipt, **_):
            log.append('observe')
            receipt.update(send_state='confirmed', generation_state='completed')
            return True
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(headless, 'prepare', AsyncMock(return_value=(page, MagicMock(), MagicMock(), []))))
        stack.enter_context(patch.object(headless, 'effort_scope', self.scope(log, restore_fails=restore_fails)))
        stack.enter_context(patch.object(package_run, 'upload_references', lambda *a, **k: record('upload')))
        stack.enter_context(patch.object(headless, 'submit_once', submit))
        stack.enter_context(patch.object(headless, 'observe', observe))
        stack.enter_context(patch.object(package_run, 'download_from_turn', lambda *a, **k: record('download')))
        stack.enter_context(patch.object(headless, 'verify_library', lambda *a, **k: record('library')))
        self.addCleanup(stack.close)

    async def run_one(self, receipt, stop=None, slots=None, effort=None, verify=False):
        return await package_run.run_one(MagicMock(), receipt, wait_seconds=60, verify_library=verify,
                                         slots=slots or asyncio.Semaphore(1), stop=stop or asyncio.Event(), effort=effort)

    async def test_the_chip_is_changed_before_upload_and_restored_after_download_and_library_check(self):
        (receipt,) = self.receipts()
        log = []
        self.patched(log)
        summary = await self.run_one(receipt, effort='instant', verify=True)
        self.assertEqual(log, [('enter', 'instant'), 'upload', 'submit', 'observe', 'download', 'library', ('exit', 'instant')])
        self.assertEqual(list(summary['phases'])[:4], ['start', 'composer_ready', 'effort_ready', 'upload_ready'])

    async def test_without_effort_nothing_is_requested_and_no_effort_phase_appears(self):
        (receipt,) = self.receipts()
        log = []
        self.patched(log)
        summary = await self.run_one(receipt)
        self.assertEqual(log[0], ('enter', None))
        self.assertNotIn('effort_ready', summary['phases'])

    async def test_a_chip_that_could_not_be_restored_stops_the_queued_packages(self):
        first, second = self.receipts(2)
        log = []
        self.patched(log, restore_fails=True)
        stop, slots = asyncio.Event(), asyncio.Semaphore(1)
        results = await asyncio.gather(self.run_one(first, stop, slots, 'instant'), self.run_one(second, stop, slots, 'instant'))
        self.assertTrue(stop.is_set())
        self.assertEqual(results[0]['reason'], 'effort_restore_failed')
        self.assertEqual(results[0]['effort']['restore_state'], 'failed')
        self.assertEqual(results[0]['send_state'], 'confirmed')          # the finished run keeps its result
        self.assertEqual((results[1]['send_state'], second.data['reason']), ('not_sent', 'batch_stopped_before_send'))
        self.assertEqual(log.count('submit'), 1)

    def test_cli_makes_effort_runs_sequential_and_refuses_parallel_ones(self):
        def cli(*argv):
            with patch.object(package_run, 'create_runs', return_value=[]), \
                 patch.object(package_run, 'run_all', new=AsyncMock(return_value={'runs': []})) as run_all, \
                 contextlib.redirect_stdout(io.StringIO()):
                code = package_run.main(['--package', 'x', '--download-dir', 'y', *argv])
            return code, run_all.call_args.args[0]
        code, args = cli('--effort', 'Instant')
        self.assertEqual((code, args.effort, args.concurrency), (0, 'instant', 1))
        self.assertEqual(cli('--effort', 'medium', '--concurrency', '1')[1].concurrency, 1)
        self.assertEqual((cli()[1].effort, cli()[1].concurrency), (None, 2))
        for bad in (['--effort', 'instant', '--concurrency', '2'], ['--effort', 'high']):
            with self.subTest(bad=bad), patch.object(package_run, 'create_runs', return_value=[]), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    package_run.main(['--package', 'x', '--download-dir', 'y', *bad])


class StopAtTheSendBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """A package already inside submit_once when another one ends unverified must still stay `not_sent` (concurrency > 1)."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def receipts(self, count):
        return package_run.create_runs(self.root / 'state', [make_package(self.root)] * count, self.root / 'out')

    def parts(self):
        editor = MagicMock(fill=AsyncMock(), inner_text=AsyncMock(return_value='Edit the canvas.'))
        send = MagicMock(is_enabled=AsyncMock(return_value=True), get_attribute=AsyncMock(return_value='false'), click=AsyncMock())
        page = MagicMock(url='https://chatgpt.com/')
        return editor, send, page

    async def test_submit_once_refuses_before_the_durable_intent_when_the_batch_is_stopped(self):
        (receipt,) = self.receipts(1)
        editor, send, page = self.parts()
        stop = asyncio.Event()
        stop.set()
        with patch.object(headless, 'required', AsyncMock(return_value=send)):
            with self.assertRaises(ResearchError) as error:
                await headless.submit_once(page, editor, MagicMock(), receipt, stop=stop)
        self.assertEqual(error.exception.code, 'batch_stopped_before_send')
        self.assertEqual((receipt.data['send_state'], receipt.data['send_clicks']), ('not_sent', 0))
        send.click.assert_not_awaited()
        self.assertEqual(headless.classify_error('batch_stopped_before_send'), 'busy')
        self.assertTrue(headless.retry_safe(receipt.data, 'busy'))

    async def test_a_package_waiting_inside_submit_once_does_not_send_after_another_one_ends_unverified(self):
        waiting, finishing = self.receipts(2)
        editor, send, page = self.parts()
        finishing_done = asyncio.Event()
        scopes = [MagicMock(name='scope-waiting'), MagicMock(name='scope-finishing')]
        order = iter(scopes)
        held = scopes[0].locator.return_value

        async def prepare(browser, **_):
            return page, editor, next(order), []

        async def required(locator, code, **_):
            if locator is held:                               # the first package is held at the Send control ...
                await finishing_done.wait()                   # ... until the other one has ended unverified
            return send

        async def observe(page, receipt, **_):
            receipt.update(send_state='unknown', reason='posted_prompt_unverified')
            finishing_done.set()
            return False

        with patch.object(headless, 'prepare', prepare), patch.object(headless, 'required', required), \
             patch.object(headless, 'observe', observe), patch.object(package_run, 'upload_references', AsyncMock(return_value=[])), \
             patch.object(headless, 'validate_attachment_order', AsyncMock()):
            stop, slots = asyncio.Event(), asyncio.Semaphore(2)              # concurrency 2: both are in flight
            # The waiting package starts first and suspends inside submit_once; only then does the other one run to its end.
            results = await asyncio.gather(
                *[package_run.run_one(MagicMock(), r, wait_seconds=60, verify_library=False, slots=slots, stop=stop)
                  for r in (waiting, finishing)])
        self.assertTrue(stop.is_set())
        self.assertEqual((results[1]['send_state'], results[1]['send_clicks']), ('unknown', 1))
        self.assertEqual((results[0]['send_state'], results[0]['send_clicks'], results[0]['reason']),
                         ('not_sent', 0, 'batch_stopped_before_send'))
        self.assertEqual(send.click.await_count, 1)                           # exactly one click in the whole batch


class BatchFailureTests(unittest.TestCase):
    """A failure around the batch (session teardown, a crash after a Send) must never read as "safe to retry" when a package was sent."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def main(self, run_all=None, *, packages=('a',), create_runs=None):
        argv = ['--state-dir', str(self.root / 'state'), '--download-dir', str(self.root / 'out'), '--concurrency', '1']
        for name in packages:
            argv += ['--package', str(make_package(self.root, name))]
        patches = []
        if run_all is not None:
            patches.append(patch.object(package_run, 'run_all', run_all))
        if create_runs is not None:
            patches.append(patch.object(package_run, 'create_runs', create_runs))
        out = io.StringIO()
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(out):
            for item in patches:
                stack.enter_context(item)
            code = package_run.main(argv)
        return code, json.loads(out.getvalue())

    def stored(self, run_id):
        return json.loads((self.root / 'state/headless-images' / run_id / 'receipt.json').read_text())

    @staticmethod
    def failing(error, **states):
        """A run_all that leaves the receipts in the given states (by index) and then raises."""
        async def run_all(args, receipts):
            for index, fields in states.items():
                receipts[int(index[1:])].update(**fields)
            raise error
        return run_all

    def test_teardown_failure_after_a_confirmed_turn_is_not_retry_safe(self):
        confirmed = {'send_state': 'confirmed', 'send_clicks': 1}
        code, result = self.main(self.failing(TimeoutError('browser.close hung'), r0=confirmed))
        self.assertEqual((code, result['error'], result['error_kind'], result['retry_safe']), (2, 'TimeoutError', 'other', False))
        self.assertEqual([(r['send_state'], r['send_clicks'], r['retry_safe']) for r in result['runs']], [('confirmed', 1, False)])
        self.assertEqual(self.stored(result['runs'][0]['run_id'])['send_state'], 'confirmed')           # the error does not rewrite it

    def test_a_receipt_left_at_intent_becomes_unknown_and_keeps_an_earlier_reason(self):
        code, result = self.main(self.failing(RuntimeError('crash'), r0={'send_state': 'intent', 'send_clicks': 1}))
        self.assertEqual((code, result['retry_safe']), (2, False))
        saved = self.stored(result['runs'][0]['run_id'])
        self.assertEqual((saved['send_state'], saved['reason'], saved['send_clicks']), ('unknown', 'RuntimeError', 1))
        code, result = self.main(self.failing(RuntimeError('crash'), r0={'send_state': 'intent', 'send_clicks': 1,
                                                                        'reason': 'send_click_uncertain'}), packages=('b',))
        self.assertEqual(self.stored(result['runs'][0]['run_id'])['reason'], 'send_click_uncertain')

    def test_nothing_was_sent_so_the_failure_is_still_retry_safe(self):
        code, result = self.main(self.failing(ResearchError('profile_busy', 'busy')))
        self.assertEqual((code, result['error'], result['error_kind'], result['retry_safe']), (2, 'profile_busy', 'busy', True))
        self.assertEqual([(r['send_state'], r['retry_safe']) for r in result['runs']], [('not_sent', True)])

    def test_failure_before_any_receipt_exists_is_retry_safe_and_has_no_runs(self):
        def refuse(*_, **__):
            raise ResearchError('package_invalid', 'bad package')
        code, result = self.main(create_runs=refuse)
        self.assertEqual((code, result['error'], result['retry_safe']), (2, 'package_invalid', True))
        self.assertNotIn('runs', result)

    def test_snapshot_failure_precedes_receipt_creation(self):
        def fail_before_commit(staging, _paths):
            self.assertFalse((Path(staging).parent / 'receipt.json').exists())
            raise ResearchError('reference_file_invalid', 'changed during snapshot')
        with patch.object(package_run, 'snapshot_references', side_effect=fail_before_commit):
            code, result = self.main()
        self.assertEqual((code, result['error'], result['retry_safe']), (2, 'reference_file_invalid', True))
        self.assertNotIn('runs', result)
        self.assertEqual(list((self.root / 'state/headless-images').glob('*/receipt.json')), [])
        self.assertEqual(list((self.root / 'state/headless-images').glob('*/.references-*')), [])

    def test_later_package_snapshot_failure_reports_only_prior_committed_runs(self):
        snapshot = package_run.snapshot_references
        calls = 0
        def fail_second(staging, paths):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.assertFalse((Path(staging).parent / 'receipt.json').exists())
                raise ResearchError('reference_file_invalid', 'second package changed during snapshot')
            return snapshot(staging, paths)
        with patch.object(package_run, 'snapshot_references', side_effect=fail_second):
            code, result = self.main(packages=('first', 'second'))
        self.assertEqual((code, result['error'], result['retry_safe']), (2, 'reference_file_invalid', True))
        self.assertEqual(len(result['runs']), 1)
        run = result['runs'][0]
        self.assertEqual((run['send_state'], run['send_clicks'], run['reference_state'], run['retry_safe']),
                         ('not_sent', 0, 'ready', True))
        self.assertTrue(Path(run['receipt']).is_file())
        self.assertEqual(len(list((self.root / 'state/headless-images').glob('*/receipt.json'))), 1)

    def test_receipt_commit_failure_removes_staged_snapshots(self):
        with patch.object(package_run.Receipt, 'create',
                          side_effect=ResearchError('receipt_write_failed', 'disk error')):
            code, result = self.main()
        self.assertEqual((code, result['error'], result['retry_safe']), (2, 'receipt_write_failed', True))
        self.assertNotIn('runs', result)
        self.assertEqual(list((self.root / 'state/headless-images').glob('*/receipt.json')), [])
        self.assertEqual(list((self.root / 'state/headless-images').glob('*/references')), [])

    def test_a_mixed_batch_is_not_retry_safe_as_a_whole_but_says_which_package_is(self):
        code, result = self.main(self.failing(TimeoutError('teardown'), r1={'send_state': 'confirmed', 'send_clicks': 1}),
                                 packages=('a', 'b'))
        self.assertEqual((code, result['retry_safe']), (2, False))
        self.assertEqual([r['retry_safe'] for r in result['runs']], [True, False])
        self.assertEqual([r['send_state'] for r in result['runs']], ['not_sent', 'confirmed'])

    def test_a_kind_that_never_retries_wins_even_when_nothing_was_sent(self):
        code, result = self.main(self.failing(ResearchError('run_exists_use_resume', 'exists')))
        self.assertEqual((result['error_kind'], result['retry_safe']), ('run_state', False))
        self.assertEqual(result['runs'][0]['send_state'], 'not_sent')


class DownloadFromTurnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.browser = headless.Browser(self.root, headless.load_config(headless.DEFAULT_STATE))
        await self.browser.start(headless=True, accept_downloads=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.context.new_page()
        await self.html('<main></main>')
        self.blue = await self.canvas('blue')
        self.red = await self.canvas('red')

    async def html(self, content):
        fixture = self.root / 'fixture.html'
        fixture.write_text(content)
        await self.page.goto(fixture.as_uri(), wait_until='domcontentloaded')

    async def canvas(self, colour, width=128, height=128):
        encoded = await self.page.evaluate(
            "([w,h,c]) => {const k=document.createElement('canvas');k.width=w;k.height=h;const x=k.getContext('2d');x.fillStyle=c;x.fillRect(0,0,w,h);return k.toDataURL().split(',')[1]}",
            [width, height, colour])
        return base64.b64decode(encoded)

    async def conversation(self, *, output=None, button='Download', buttons=1, previews=1):
        original = base64.b64encode(self.blue).decode('ascii')
        out = base64.b64encode(output or self.blue).decode('ascii')
        images = ''.join(f'<div data-testid="generated-image-preview"><img class="thumb" alt="Generated image {i}" width="300" src="data:image/png;base64,{original}"></div>' for i in range(previews))
        await self.html(f'''<main><div data-testid="generated-image-gallery">{images}</div><div id="viewer"></div>
<script>document.querySelector('.thumb').onclick=()=>{{
 const big=new Image();big.alt='viewer';big.width=600;big.src=document.querySelector('.thumb').src;viewer.append(big);
 for(let i=0;i<{buttons};i++){{const b=document.createElement('button');b.setAttribute('aria-label','{button}');b.textContent='d';
  b.onclick=()=>{{const a=document.createElement('a');a.href='data:image/png;base64,{out}';a.download='Image.png';a.click();}};viewer.append(b);}}
}};</script></main>''')
        await self.page.wait_for_function("() => [...document.images].every(i=>i.complete&&i.naturalWidth===128)")
        fingerprint = await image_files.decode_image(self.page, self.blue, 'image/png')
        receipt = headless.Receipt(self.root / 'state/run/receipt.json')
        receipt.create('turn-run', 'Create a blue square')
        receipt.update(send_state='confirmed', send_clicks=1, generation_state='completed', image_dimensions=[[128, 128]],
                       image_fingerprints=[fingerprint], download_stem=str(self.root / 'out/turn-run'))
        return receipt

    async def test_original_file_is_saved_from_the_owned_turn_without_the_library(self):
        receipt = await self.conversation()
        await package_run.download_from_turn(self.page, receipt)
        saved = self.root / 'out/turn-run.png'
        self.assertEqual(saved.read_bytes(), self.blue)
        original = receipt.data['original_file']
        self.assertEqual(original['sha256'], hashlib.sha256(self.blue).hexdigest())
        self.assertEqual(original['dimensions'], [128, 128])
        self.assertTrue(receipt.data['downloaded'])
        self.assertFalse(receipt.data['library_verified'])
        self.assertEqual(receipt.data['send_clicks'], 1)
        self.assertEqual(list((self.root / 'out').glob('.chatgpt-original-*')), [])

    async def test_download_file_named_download_file_is_also_found(self):
        receipt = await self.conversation(button='Download file')
        await package_run.download_from_turn(self.page, receipt)
        self.assertTrue((self.root / 'out/turn-run.png').is_file())

    async def test_package_publication_interruption_recovers_without_browser_download(self):
        receipt=await self.conversation()
        update=receipt.update
        def interrupt_completion(**fields):
            if fields.get('download_state')=='completed':raise OSError('simulated interruption')
            return update(**fields)
        with patch.object(receipt,'update',interrupt_completion):
            with self.assertRaises(OSError):await package_run.download_from_turn(self.page,receipt)
        retained=headless.Receipt(receipt.path)
        self.assertEqual(retained.data['download_state'],'publishing')
        self.assertEqual(retained.data['download_file'],str(self.root/'out/turn-run.png'))
        await self.page.close()
        self.assertTrue(image_files.reconcile_original(retained))
        self.assertEqual((self.root/'out/turn-run.png').read_bytes(),self.blue)
        self.assertTrue(retained.data['downloaded'])

    async def test_a_different_raster_is_not_published(self):
        receipt = await self.conversation(output=self.red)
        with self.assertRaises(headless.ResearchError) as error:
            await package_run.download_from_turn(self.page, receipt)
        self.assertEqual(error.exception.code, 'download_identity_unverified')
        self.assertEqual(list((self.root / 'out').glob('*')), [])
        self.assertFalse(receipt.data['downloaded'])

    async def test_ambiguous_download_control_or_two_images_block_before_any_download(self):
        for kwargs, code in (({'buttons': 2}, 'download_control_unverified'), ({'previews': 2}, 'download_identity_unverified')):
            receipt = await self.conversation(**kwargs)
            receipt.path.unlink()
            with self.assertRaises(headless.ResearchError) as error:
                await package_run.download_from_turn(self.page, receipt)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(list((self.root / 'out').glob('*')), [])

    async def test_existing_target_is_preserved(self):
        receipt = await self.conversation()
        target = self.root / 'out/turn-run.png'
        target.parent.mkdir()
        target.write_bytes(b'previous artifact')
        with self.assertRaises(headless.ResearchError) as error:
            await package_run.download_from_turn(self.page, receipt)
        self.assertEqual(error.exception.code,'download_target_exists')
        self.assertNotEqual(receipt.data.get('download_state'),'intent')
        self.assertEqual(target.read_bytes(), b'previous artifact')
        self.assertEqual(list(target.parent.glob('.chatgpt-original-*')), [])

    async def test_turn_state_reads_the_preview_badge_and_stop_control_of_the_owned_turn(self):
        prompt = 'Show a preview of a vase'
        original = base64.b64encode(self.blue).decode('ascii')
        await self.html(f'''<main><div data-turn-key="t1"><div data-user-message-bubble>{prompt}</div>
<div data-testid="generated-image-gallery"><div data-testid="generated-image-preview">
<img alt="Generated image 1" width="300" src="data:image/png;base64,{original}"><span id="badge">Preview</span></div></div></div>
<button id="stop" data-testid="stop-button">Stop</button></main>''')
        await self.page.wait_for_function("() => document.images[0].complete&&document.images[0].naturalWidth===128")
        read = lambda: self.page.evaluate(headless.TURN_STATE, {'prompt': prompt, 'stop': headless.STOP})
        view = await read()
        self.assertEqual((view['matches'], view['preview'], view['generating'], len(view['images'])), (1, True, True, 1))
        await self.page.evaluate("() => document.getElementById('badge').remove()")
        view = await read()
        self.assertEqual((view['preview'], view['generating']), (False, True))
        await self.page.evaluate("() => document.getElementById('stop').remove()")
        view = await read()
        self.assertEqual((view['preview'], view['generating']), (False, False))

    async def test_any_stop_labelled_control_counts_as_generating(self):
        await self.html('<main><button aria-label="Stop image generation">x</button></main>')
        self.assertTrue(await self.page.evaluate("s => [...document.querySelectorAll(s)].length>0", headless.STOP))

    async def test_turn_without_one_pixel_identity_is_refused(self):
        receipt = await self.conversation()
        receipt.update(image_fingerprints=[])
        with self.assertRaises(headless.ResearchError) as error:
            await package_run.download_from_turn(self.page, receipt)
        self.assertEqual(error.exception.code, 'download_identity_unverified')


if __name__ == '__main__':
    unittest.main()
