"""Failures must retain the operation outcome and expose secondary cleanup failure."""
import argparse
import asyncio
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import headless
import image_files

class CleanupErrorsTests(unittest.IsolatedAsyncioTestCase):
    def fixtures(self, kind, state, browser):
        target = 'ChromeBrowser' if kind == 'chrome' else 'Browser'
        return patch.object(headless, target, return_value=browser), headless.session(state, browser_kind=kind)

    def assert_unlocked(self, kind, state):
        lock = headless.chrome_lock if kind == 'chrome' else headless.profile_lock
        with lock(state):
            pass

    async def test_primary_startup_and_body_errors_survive_cleanup_failure(self):
        for kind in ('cloakbrowser', 'chrome'):
            for phase in ('startup', 'body'):
                with self.subTest(kind=kind, phase=phase), tempfile.TemporaryDirectory() as fixture:
                    state = Path(fixture)
                    primary = headless.ResearchError('composer_unavailable', 'original')
                    original = {'composer': 'missing'}
                    primary.diagnostic = original
                    browser = MagicMock(start=AsyncMock(side_effect=primary if phase == 'startup' else None),
                                        close=AsyncMock(side_effect=TimeoutError('cleanup')))
                    mocked, context = self.fixtures(kind, state, browser)
                    with mocked, self.assertRaises(headless.ResearchError) as caught:
                        async with context:
                            raise primary
                    self.assertIs(caught.exception, primary)
                    self.assertEqual(primary.diagnostic, {'composer': 'missing', 'browser_cleanup': {'state': 'failed', 'error': 'TimeoutError'}})
                    self.assertEqual(original, {'composer': 'missing'})
                    self.assertIn('browser_cleanup_failed: TimeoutError', primary.__notes__)
                    browser.close.assert_awaited_once()
                    self.assert_unlocked(kind, state)

    async def test_cleanup_failure_after_success_remains_a_failure(self):
        for kind in ('cloakbrowser', 'chrome'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as fixture:
                state = Path(fixture)
                failure = TimeoutError('cleanup')
                browser = MagicMock(start=AsyncMock(), close=AsyncMock(side_effect=failure))
                mocked, context = self.fixtures(kind, state, browser)
                with mocked, self.assertRaises(TimeoutError) as caught:
                    async with context:
                        pass
                self.assertIs(caught.exception, failure)
                browser.close.assert_awaited_once()
                self.assert_unlocked(kind, state)

    async def test_body_cancellation_survives_ordinary_cleanup_failure(self):
        for kind in ('cloakbrowser', 'chrome'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as fixture:
                state = Path(fixture)
                cancellation = asyncio.CancelledError('body cancelled')
                browser = MagicMock(start=AsyncMock(), close=AsyncMock(side_effect=TimeoutError('cleanup')))
                mocked, context = self.fixtures(kind, state, browser)
                with mocked, self.assertRaises(asyncio.CancelledError) as caught:
                    async with context:
                        raise cancellation
                self.assertIs(caught.exception, cancellation)
                self.assertEqual(cancellation.diagnostic['browser_cleanup']['error'], 'TimeoutError')
                browser.close.assert_awaited_once()
                self.assert_unlocked(kind, state)

    async def test_external_cancellation_during_close_propagates_and_unlocks(self):
        for kind in ('cloakbrowser', 'chrome'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as fixture:
                state = Path(fixture)
                entered = asyncio.Event()
                hold = asyncio.Event()
                async def close():
                    entered.set()
                    await hold.wait()
                browser = MagicMock(start=AsyncMock(), close=AsyncMock(side_effect=close))
                mocked, context = self.fixtures(kind, state, browser)
                async def operation():
                    async with context:
                        pass
                with mocked:
                    task = asyncio.create_task(operation())
                    await asyncio.wait_for(entered.wait(), timeout=1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                browser.close.assert_awaited_once()
                self.assert_unlocked(kind, state)

    async def test_real_chrome_start_keeps_launch_failure_when_driver_stop_fails(self):
        with tempfile.TemporaryDirectory() as fixture:
            state = Path(fixture)
            primary = RuntimeError('persistent context launch failed')
            playwright = MagicMock(stop=AsyncMock(side_effect=TimeoutError('driver stop')))
            playwright.chromium.launch_persistent_context = AsyncMock(side_effect=primary)
            browser = image_files.ChromeBrowser(state, headless.load_config(state))
            with (patch.object(image_files, 'chrome_binary', return_value=state / 'fixture-browser'),
                  patch.object(image_files, 'start_playwright', AsyncMock(return_value=playwright)),
                  patch.object(headless, 'ChromeBrowser', return_value=browser),
                  self.assertRaises(RuntimeError) as caught):
                async with headless.session(state, browser_kind='chrome'):
                    self.fail('startup failure must not enter the body')
            self.assertIs(caught.exception, primary)
            self.assertEqual(primary.diagnostic['browser_cleanup']['error'], 'TimeoutError')
            playwright.stop.assert_awaited_once()
            self.assertIsNone(browser.playwright)
            self.assert_unlocked('chrome', state)

    async def test_login_primary_and_cleanup_failure_preserve_error_and_unlock(self):
        for kind in ('cloakbrowser', 'chrome'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as fixture:
                state = Path(fixture)
                primary = headless.ResearchError('composer_unavailable', 'login startup failed')
                browser = MagicMock(start=AsyncMock(side_effect=primary), open_login_page=AsyncMock(side_effect=primary),
                                    close=AsyncMock(side_effect=TimeoutError('cleanup')))
                target = 'ChromeBrowser' if kind == 'chrome' else 'Browser'
                operation = headless.login_chrome if kind == 'chrome' else headless.login_cloak
                with patch.object(headless, target, return_value=browser), self.assertRaises(headless.ResearchError) as caught:
                    await operation(state)
                self.assertIs(caught.exception, primary)
                self.assertEqual(primary.diagnostic['browser_cleanup']['error'], 'TimeoutError')
                browser.close.assert_awaited_once()
                self.assert_unlocked(kind, state)

class CleanupReceiptTests(unittest.TestCase):
    def test_operation_code_and_cleanup_diagnostic_reach_json_and_receipt(self):
        for kind in ('cloakbrowser', 'chrome'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as fixture:
                state = Path(fixture)
                prompt = state / 'prompt.txt'
                prompt.write_text('Fixture only; never send')
                args = argparse.Namespace(command='generate', state_dir=state, run_id='cleanup-test', prompt_file=prompt,
                                          reference=[], download_file=None, requested_size=None, pause_idle_worker=False,
                                          preview=False, wait_seconds=0, effort=None, browser=kind)
                primary = headless.ResearchError('composer_unavailable', 'original')
                primary.diagnostic = {'composer': 'missing'}
                browser = MagicMock(start=AsyncMock(), close=AsyncMock(side_effect=TimeoutError('cleanup')))
                target = 'ChromeBrowser' if kind == 'chrome' else 'Browser'
                output = io.StringIO()
                with (patch.object(headless, target, return_value=browser),
                      patch.object(headless, 'prepare', AsyncMock(side_effect=primary)),
                      patch.object(headless, 'submit_once', AsyncMock()) as submit,
                      contextlib.redirect_stdout(output)):
                    result = headless.run_command(args)
                submit.assert_not_awaited()
                printed = json.loads(output.getvalue())
                receipt = headless.Receipt(state / 'headless-images/cleanup-test/receipt.json')
                self.assertEqual(result, 2)
                self.assertEqual((printed['error'], printed['error_kind'], printed['send_state']), ('composer_unavailable', 'ui', 'not_sent'))
                expected = {'composer': 'missing', 'browser_cleanup': {'state': 'failed', 'error': 'TimeoutError'}}
                self.assertEqual(printed['diagnostic'], expected)
                self.assertEqual(receipt.data['diagnostic'], expected)
                self.assertEqual(receipt.data['send_clicks'], 0)
                browser.close.assert_awaited_once()

    def test_login_cli_reports_primary_and_cleanup_only_failures_as_json(self):
        for kind in ('cloakbrowser', 'chrome'):
            for primary_failure in (True, False):
                with self.subTest(kind=kind, primary=primary_failure), tempfile.TemporaryDirectory() as fixture:
                    state = Path(fixture)
                    primary = headless.ResearchError('composer_unavailable', 'login startup failed')
                    browser = MagicMock(start=AsyncMock(side_effect=primary if primary_failure else None),
                                        open_login_page=AsyncMock(side_effect=primary if primary_failure else None),
                                        wait_until_closed=AsyncMock(), close=AsyncMock(side_effect=TimeoutError('cleanup')))
                    browser.context.pages = []
                    browser.context.new_page = AsyncMock(return_value=MagicMock(goto=AsyncMock()))
                    target = 'ChromeBrowser' if kind == 'chrome' else 'Browser'
                    output = io.StringIO()
                    with (patch.object(headless, target, return_value=browser),
                          patch.object(sys, 'argv', ['headless.py', 'login', '--state-dir', str(state), '--browser', kind]),
                          contextlib.redirect_stdout(output)):
                        code = headless.main()
                    printed = json.loads(output.getvalue().splitlines()[-1])
                    self.assertEqual(code, 2)
                    if primary_failure:
                        self.assertEqual((printed['error'], printed['error_kind']), ('composer_unavailable', 'ui'))
                        self.assertEqual(printed['diagnostic']['browser_cleanup']['error'], 'TimeoutError')
                    else:
                        self.assertEqual((printed['error'], printed['error_kind']), ('TimeoutError', 'other'))
                    browser.close.assert_awaited_once()
                    lock = headless.chrome_lock if kind == 'chrome' else headless.profile_lock
                    with lock(state):
                        pass

if __name__ == '__main__':
    unittest.main()
