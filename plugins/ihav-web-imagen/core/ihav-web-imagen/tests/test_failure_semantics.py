"""Failure semantics of one image request, as a table: what the site shows -> what the run MUST and MUST NOT do.

Every scenario runs the real `run_command` (generate, then status/resume where it matters) with the real `submit_once` and
`observe`; only the browser surface is scripted (`page.evaluate` returns the rendered turn state, the Send button records
clicks). No ChatGPT request, profile or network is involved.

Invariants checked after every command of every scenario (so a new scenario cannot forget them):
  * at most ONE Send click exists for the run, however many commands ran, and `send_clicks` equals the clicks that happened;
  * a command never leaves `intent` behind: the run is `not_sent`, `confirmed` or `unknown`;
  * `retry_safe` is true only when no click happened; a run that clicked is never retry-safe;
  * resume and status never click.
"""

import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/headless.py'
SPEC = importlib.util.spec_from_file_location('headless_failure_semantics', SCRIPT)
headless = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(headless)

PROMPT = 'Create one square blue vase on a plain background.'
OWNED = 'https://chatgpt.com/c/owned-test'


def view(matches=1, users=1, images=0, generating=False, preview=False, alerts=(), loaded=True):
    """What the rendered page tells `observe` about the turn that carries our prompt."""
    return {'user_count': users, 'matches': matches, 'assistant_count': 1, 'turn_id': 'turn-1' if matches else None,
            'images': [{'key': f'img-{i}', 'width': 1024, 'height': 1024, 'loaded': loaded} for i in range(images)],
            'generating': generating, 'preview': preview, 'alerts': list(alerts)}


ABSENT = view(matches=0, users=0)                    # our prompt never shows
OTHER_TURN = view(matches=0, users=1)                # someone else's message is in the chat, ours is not
AMBIGUOUS = view(matches=2, users=2)                 # two candidates for our turn
POSTED = view(generating=True)
DONE = view(images=1)


def last_json(text):
    """The final (multi-line) JSON object of a command's stdout; earlier lines are single-line events."""
    decoder, found, index = json.JSONDecoder(), None, 0
    while index < len(text):
        if text[index] == '{' and (index == 0 or text[index - 1] == '\n'):
            try:
                found, end = decoder.raw_decode(text, index)
                index = end
                continue
            except ValueError:
                pass
        index += 1
    return found


class Site(unittest.TestCase):
    """A scripted ChatGPT page plus the state directory of one run."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'prompt.txt').write_text(PROMPT)
        self.clicks = 0
        self.browsers_opened = 0
        self.url = OWNED
        self.click_raises = None
        self.send_enabled = True
        self.editor_text = PROMPT
        self.chat_loads = True
        self.views = [DONE]
        self.index = 0
        self.send = MagicMock(is_enabled=AsyncMock(side_effect=lambda: self.send_enabled),
                              get_attribute=AsyncMock(return_value='false'), click=AsyncMock(side_effect=self._click))
        self.editor = MagicMock(fill=AsyncMock(), inner_text=AsyncMock(side_effect=lambda: self.editor_text))
        self.page = MagicMock(goto=AsyncMock(), evaluate=AsyncMock(side_effect=self._evaluate))
        type(self.page).url = property(lambda page: self.url)
        self.browser = MagicMock(context=MagicMock(new_page=AsyncMock(return_value=self.page)), check_access=AsyncMock())

    async def _click(self, **_):
        self.clicks += 1
        if self.click_raises:
            raise self.click_raises

    async def _evaluate(self, script, argument=None):
        if script == headless.TURN_STATE:
            current = self.views[min(self.index, len(self.views) - 1)]
            self.index += 1
            return current
        if script == headless.TURN_FINGERPRINTS:
            return [{'key': 'img-0', 'width': 1024, 'height': 1024, 'loaded': True, 'pixel_sha256': 'f' * 64}]
        raise AssertionError('unexpected page script')

    def show(self, *views):
        self.views, self.index = list(views), 0

    async def _required(self, locator, code, **_):
        if code == 'owned_chat_not_loaded' and not self.chat_loads:
            raise headless.ResearchError('owned_chat_not_loaded', 'The owned chat did not load')
        return self.send

    async def _verify(self, browser, page, receipt):
        receipt.update(library_verified=True, library_verification_method='scripted')

    def cli(self, command, *, run_id='run-1', wait=0.3):
        args = argparse.Namespace(command=command, state_dir=self.root, run_id=run_id, prompt_file=self.root / 'prompt.txt',
                                  reference=[], download_file=None, requested_size=None, pause_idle_worker=False,
                                  preview=False, wait_seconds=wait, effort=None)

        @contextlib.asynccontextmanager
        async def local_session(*_, **__):
            self.browsers_opened += 1
            yield self.browser

        async def prepare(browser, **_):
            return self.page, self.editor, MagicMock(), []

        out = io.StringIO()
        with patch.object(headless, 'session', local_session), patch.object(headless, 'prepare', prepare), \
             patch.object(headless, 'required', self._required), patch.object(headless, 'verify_library', self._verify), \
             patch.object(headless, 'validate_attachment_order', AsyncMock()), \
             patch.object(headless, 'POLL_SECONDS', 0.01), contextlib.redirect_stdout(out):
            code = headless.run_command(args)
        result = last_json(out.getvalue())
        self.check_invariants(command, run_id, result)
        return code, result

    def receipt(self, run_id='run-1'):
        return headless.Receipt(self.root / 'headless-images' / run_id / 'receipt.json').data

    def check_invariants(self, command, run_id, result):
        data = self.receipt(run_id)
        self.assertLessEqual(self.clicks, 1, 'a run must never click Send twice')
        if data is not None:
            self.assertEqual(data.get('send_clicks', 0), self.clicks, 'send_clicks must equal the clicks that happened')
            self.assertIn(data['send_state'], {'not_sent', 'confirmed', 'unknown'}, 'a finished command must not leave `intent`')
            if self.clicks:
                self.assertFalse(result['retry_safe'], 'a run that clicked Send is never retry-safe')
            else:
                self.assertEqual(data['send_state'], 'not_sent')
        if command in ('resume', 'status'):
            self.assertEqual(result.get('send_clicks', 0), self.clicks)


class BeforeSendTests(Site):
    """Nothing was clicked: the failure is retry-safe and the receipt says `not_sent`."""

    def assert_not_sent(self, code, result, error):
        self.assertEqual((code, result['error'], result['send_state'], result['retry_safe']), (2, error, 'not_sent', True))
        self.assertEqual(self.clicks, 0)

    def test_send_control_disabled(self):
        self.send_enabled = False
        code, result = self.cli('generate')
        self.assert_not_sent(code, result, 'send_not_ready')

    def test_editor_does_not_hold_the_exact_prompt(self):
        self.editor_text = 'Something else entirely'
        code, result = self.cli('generate')
        self.assert_not_sent(code, result, 'draft_mismatch')
        self.assertEqual(result['error_kind'], 'ui')

    def test_repeating_generate_opens_no_browser_and_does_not_click(self):
        self.show(POSTED)
        self.send_enabled = False
        self.cli('generate')
        opened = self.browsers_opened
        code, result = self.cli('generate')
        self.assertEqual((code, result['error'], result['error_kind']), (2, 'run_exists_use_resume', 'run_state'))
        self.assertEqual((self.browsers_opened, self.clicks), (opened, 0))
        self.assertFalse(result['retry_safe'])                     # the run exists: resume it, never start a new one

    def test_status_never_opens_a_browser(self):
        self.send_enabled = False
        self.cli('generate')
        opened = self.browsers_opened
        code, result = self.cli('status')
        self.assertEqual((code, result['send_state'], self.browsers_opened), (0, 'not_sent', opened))


class AfterClickTests(Site):
    """A click happened. What the run may conclude depends on what the page then shows."""

    def setUp(self):
        super().setUp()
        self.show(POSTED, DONE, DONE, DONE, DONE, DONE, DONE)

    def test_clean_run_is_confirmed_completed_and_verified(self):
        code, result = self.cli('generate')
        self.assertEqual((code, result['send_state'], result['generation_state'], result['library_verified'],
                          result['send_clicks']), (0, 'confirmed', 'completed', True, 1))
        self.assertFalse(result['retry_safe'])

    def test_prompt_never_shows_is_unknown_not_a_licence_to_resend(self):
        self.show(ABSENT)
        code, result = self.cli('generate')
        self.assertEqual((code, result['send_state'], result['reason'], result['send_clicks']), (2, 'unknown', 'posted_prompt_unverified', 1))
        self.assertFalse(result['retry_safe'])
        self.assertEqual(result['observation_last']['matches'], 0)

    def test_a_different_message_in_the_chat_is_not_our_turn(self):
        self.show(OTHER_TURN)
        code, result = self.cli('generate')
        self.assertEqual((code, result['send_state'], result['reason']), (2, 'unknown', 'posted_prompt_unverified'))
        self.assertEqual(result['observation_last']['user_count'], 1)

    def test_two_candidates_for_our_turn_is_ambiguous_and_ends_at_once(self):
        self.show(AMBIGUOUS)
        code, result = self.cli('generate')
        self.assertEqual((code, result['send_state'], result['reason']), (2, 'unknown', 'turn_ownership_ambiguous'))

    def test_visible_alert_before_our_turn_shows(self):
        self.show(view(matches=0, users=0, alerts=['You have reached a limit']))
        code, result = self.cli('generate')
        self.assertEqual((code, result['send_state'], result['reason']), (2, 'unknown', 'visible_chatgpt_alert'))

    def test_visible_alert_after_our_turn_keeps_it_confirmed(self):
        self.show(view(generating=True, alerts=['Something went wrong']))
        code, result = self.cli('generate')
        self.assertEqual((code, result['send_state'], result['reason']), (2, 'confirmed', 'visible_chatgpt_alert'))
        self.assertNotEqual(result.get('generation_state'), 'completed')

    def test_still_generating_at_the_deadline_is_pending_not_failed_or_resent(self):
        self.show(POSTED)
        code, result = self.cli('generate')
        self.assertEqual((code, result['send_state'], result['generation_state']), (2, 'confirmed', 'pending'))

    def test_progressive_preview_is_never_taken_as_the_final_image(self):
        self.show(view(images=1, preview=True))
        code, result = self.cli('generate')
        self.assertEqual((code, result['generation_state']), (2, 'pending'))

    def test_an_image_that_has_not_loaded_is_not_final(self):
        self.show(view(images=1, loaded=False))
        code, result = self.cli('generate')
        self.assertEqual((code, result['generation_state']), (2, 'pending'))

    def test_image_present_while_the_stop_control_shows_is_not_final(self):
        self.show(view(images=1, generating=True))
        code, result = self.cli('generate')
        self.assertEqual((code, result['generation_state']), (2, 'pending'))

    def test_the_turn_vanishing_after_confirmation_does_not_downgrade_the_run(self):
        self.show(POSTED, ABSENT)
        code, result = self.cli('generate')
        self.assertEqual((code, result['send_state']), (2, 'confirmed'))

    def test_click_error_is_ambiguous_and_never_replayed(self):
        self.click_raises = TimeoutError('no acknowledgement')
        code, result = self.cli('generate')
        self.assertEqual((code, result['error'], result['error_kind'], result['send_state'], result['send_clicks']),
                         (2, 'send_click_uncertain', 'ambiguous_send', 'unknown', 1))
        self.assertFalse(result['retry_safe'])
        self.assertEqual(result['conversation_url'], OWNED)
        code, again = self.cli('generate')                            # asking again only points at resume
        self.assertEqual((again['error'], self.clicks), ('run_exists_use_resume', 1))


class ResumeTests(Site):
    """Recovery observes the owned chat. It never types, clicks or creates a second request."""

    def unknown_run(self, url=OWNED):
        self.url = url
        self.show(ABSENT)
        code, result = self.cli('generate')
        self.assertEqual((result['send_state'], result['send_clicks']), ('unknown', 1))
        return result

    def test_resume_finds_the_finished_image_without_clicking_again(self):
        self.unknown_run()
        self.show(DONE)
        code, result = self.cli('resume')
        self.assertEqual((code, result['send_state'], result['generation_state'], result['recovered'], result['send_clicks']),
                         (0, 'confirmed', 'completed', True, 1))
        self.assertIsNone(result['generation_seconds'])               # a recovered run has no honest generation time

    def test_resume_twice_is_idempotent(self):
        self.unknown_run()
        self.show(POSTED)
        self.cli('resume')
        self.show(DONE)
        code, result = self.cli('resume')
        self.assertEqual((code, result['generation_state'], result['send_clicks']), (0, 'completed', 1))

    def test_resume_without_a_retained_conversation_does_not_regenerate(self):
        self.unknown_run(url='https://chatgpt.com/')
        opened = self.browsers_opened
        code, result = self.cli('resume')
        self.assertEqual((code, result['error'], result['error_kind'], result['send_state']), (2, 'recovery_url_missing', 'run_state', 'unknown'))
        self.assertFalse(result['retry_safe'])
        self.assertEqual(self.clicks, 1)
        self.assertEqual(self.browser.context.new_page.await_count, 0)

    def test_resume_when_the_owned_chat_does_not_load_keeps_the_run_unknown(self):
        self.unknown_run()
        self.chat_loads = False
        code, result = self.cli('resume')
        self.assertEqual((code, result['error'], result['send_state']), (2, 'owned_chat_not_loaded', 'unknown'))
        self.assertFalse(result['retry_safe'])

    def test_resume_that_finds_two_candidates_stays_unknown(self):
        self.unknown_run()
        self.show(AMBIGUOUS)
        code, result = self.cli('resume')
        self.assertEqual((code, result['send_state'], result['reason']), (2, 'unknown', 'turn_ownership_ambiguous'))

    def test_resume_of_a_confirmed_pending_run_keeps_waiting_without_a_click(self):
        self.show(POSTED)
        self.cli('generate')
        self.show(POSTED)
        code, result = self.cli('resume')
        self.assertEqual((code, result['send_state'], result['generation_state'], result['send_clicks']), (2, 'confirmed', 'pending', 1))

    def test_a_run_that_was_never_sent_is_the_only_kind_resume_may_send(self):
        self.send_enabled = False
        self.cli('generate')                                          # refused before intent: not_sent
        self.send_enabled = True
        self.show(POSTED, DONE, DONE, DONE, DONE, DONE, DONE)
        code, result = self.cli('resume')
        self.assertEqual((code, result['send_state'], result['send_clicks']), (0, 'confirmed', 1))
        self.show(DONE)
        code, result = self.cli('resume')                             # and now it has been sent: never again
        self.assertEqual((code, result['send_clicks'], self.clicks), (0, 1, 1))

    def test_a_never_sent_run_with_missing_references_is_not_replaced_by_a_text_only_request(self):
        self.send_enabled = False
        self.cli('generate')
        path = self.root / 'headless-images/run-1/receipt.json'
        receipt = headless.Receipt(path)
        receipt.update(required_reference_count=2, references=[], reference_state='preparing')
        self.send_enabled = True
        code, result = self.cli('resume')
        self.assertEqual((code, result['error'], result['send_state'], self.clicks), (2, 'reference_preparation_incomplete', 'not_sent', 0))
        self.assertEqual(result['error_kind'], 'run_state')


class LastLineOfDefenseTests(Site):
    """`submit_once` itself refuses to start a second request, whatever the caller did before reaching it."""

    def test_no_state_other_than_not_sent_may_type_or_click(self):
        for state in ('intent', 'unknown', 'confirmed'):
            with self.subTest(state=state):
                receipt = headless.Receipt(self.root / f'headless-images/direct-{state}/receipt.json')
                receipt.create(f'direct-{state}', PROMPT)
                receipt.update(send_state=state, send_clicks=1)
                self.clicks = 1                                        # the click this state stands for
                with patch.object(headless, 'required', self._required):
                    with self.assertRaises(headless.ResearchError) as error:
                        asyncio.run(headless.submit_once(self.page, self.editor, MagicMock(), receipt))
                self.assertEqual(error.exception.code, 'resend_forbidden')
                self.assertEqual(headless.classify_error('resend_forbidden'), 'ambiguous_send')
                self.editor.fill.assert_not_awaited()
                self.assertEqual((self.clicks, receipt.data['send_state'], receipt.data['send_clicks']), (1, state, 1))


class RetryVerdictTests(unittest.TestCase):
    """The verdict other agents act on: True only when no request was submitted."""

    def test_table(self):
        cases = [
            ('no receipt yet', None, None, True),
            ('refused before intent', {'send_state': 'not_sent', 'send_clicks': 0}, 'ui', True),
            ('intent left by a crash', {'send_state': 'intent', 'send_clicks': 1}, 'other', False),
            ('click outcome unknown', {'send_state': 'unknown', 'send_clicks': 1}, 'ambiguous_send', False),
            ('confirmed and pending', {'send_state': 'confirmed', 'send_clicks': 1}, 'ui', False),
            ('unknown with no kind recorded', {'send_state': 'unknown', 'send_clicks': 1}, None, False),
            ('never sent but the run exists', {'send_state': 'not_sent', 'send_clicks': 0}, 'run_state', False),
            ('click counted while not_sent', {'send_state': 'not_sent', 'send_clicks': 1}, 'ui', False),
            ('unknown although the click counter was lost', {'send_state': 'unknown', 'send_clicks': 0}, None, False),
            ('intent although the click counter was lost', {'send_state': 'intent'}, None, False),
        ]
        for name, data, kind, expected in cases:
            with self.subTest(name):
                self.assertIs(headless.retry_safe(data, kind), expected)


if __name__ == '__main__':
    unittest.main()
