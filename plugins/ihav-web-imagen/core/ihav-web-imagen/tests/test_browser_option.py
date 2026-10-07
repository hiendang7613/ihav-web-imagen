"""`--browser chrome`: plain Google Chrome in a profile of its own, next to the default CloakBrowser.

Offline: Playwright, Chrome and ChatGPT are replaced by fakes, so no browser starts and nothing is sent. The tests pin what the
option promises: CloakBrowser stays the default, a run is resumed in the browser it was created with, the Chrome profile is
its own (never a copy of an everyday one), the launch carries no anti-detection arguments, and a missing Chrome fails before
any Send.
"""
import argparse
import asyncio
import builtins
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import imagine
import package_run
from package_run import headless      # puts the runtime on sys.path, so it must come before image_files
import image_files


class FakeContext:
    def __init__(self):
        self.closed = False
        self.pages = []

    def set_default_timeout(self, value):
        pass

    def set_default_navigation_timeout(self, value):
        pass

    async def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, fail=None):
        self.launches, self.stopped, self.fail = [], False, fail
        self.context = FakeContext()
        playwright = self

        class Chromium:
            async def launch_persistent_context(self, **options):
                playwright.launches.append(options)
                if playwright.fail:
                    raise playwright.fail
                return playwright.context
        self.chromium = Chromium()

    async def stop(self):
        self.stopped = True


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.chrome = self.root / 'Google Chrome'
        self.chrome.write_text('#!/bin/sh\n')
        env = patch.dict(os.environ, {'IHAV_WEB_IMAGEN_CHROME': str(self.chrome)})
        env.start()
        self.addCleanup(env.stop)


class LauncherTests(Workspace):
    def start(self, fake, **options):
        async def go():
            with patch.object(image_files, 'start_playwright', AsyncMock(return_value=fake)):
                browser = image_files.ChromeBrowser(self.root / 'state', MagicMock())
                await browser.start(**options)
                return browser
        return asyncio.run(go())

    def test_it_launches_plain_chrome_in_a_profile_of_its_own_with_playwrights_defaults_only(self):
        fake = FakePlaywright()
        self.start(fake, headless=True, accept_downloads=True)
        (options,) = fake.launches
        self.assertEqual(options['user_data_dir'], str(self.root / 'state' / 'chrome-profile'))
        self.assertEqual(options['executable_path'], str(self.chrome))
        self.assertTrue(options['headless'])
        self.assertTrue(options['accept_downloads'])
        # No anti-detection options of any kind: the whole contract of "plain Chrome".
        self.assertEqual(set(options), {'user_data_dir', 'executable_path', 'headless', 'viewport', 'accept_downloads'})
        self.assertFalse((self.root / 'state' / 'profile').exists())               # never the CloakBrowser profile

    def test_the_profile_is_private_and_separate_from_the_users_own_chrome(self):
        self.start(FakePlaywright(), headless=True)
        profile = self.root / 'state' / 'chrome-profile'
        self.assertEqual(profile.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('Application Support/Google/Chrome', str(profile))

    def test_closing_stops_playwright_and_a_failed_launch_cleans_up_too(self):
        fake = FakePlaywright()
        browser = self.start(fake, headless=True)
        asyncio.run(browser.close())
        self.assertTrue(fake.context.closed and fake.stopped)
        failing = FakePlaywright(fail=RuntimeError('launch failed'))
        with self.assertRaises(RuntimeError):
            self.start(failing, headless=True)
        self.assertTrue(failing.stopped)

    def test_a_missing_chrome_is_an_environment_error_before_anything_starts(self):
        started = AsyncMock(side_effect=AssertionError('Playwright must not start without Chrome'))
        with patch.dict(os.environ, {'IHAV_WEB_IMAGEN_CHROME': str(self.root / 'nowhere')}), \
             patch.object(image_files, 'start_playwright', started):
            with self.assertRaises(image_files.ResearchError) as error:
                asyncio.run(image_files.ChromeBrowser(self.root / 'state', MagicMock()).start(headless=True))
        self.assertEqual(error.exception.code, 'browser_binary_missing')
        self.assertEqual(headless.classify_error(error.exception.code), 'environment')

    def test_an_explicit_chrome_path_is_never_second_guessed(self):
        with patch.dict(os.environ, {'IHAV_WEB_IMAGEN_CHROME': str(self.root / 'nowhere')}):
            self.assertIsNone(image_files.chrome_binary())


class SessionTests(Workspace):
    def test_a_chrome_session_does_not_take_the_cloakbrowser_profile_lock(self):
        browser = MagicMock(start=AsyncMock(), close=AsyncMock())
        cloak_lock = MagicMock(side_effect=AssertionError('Chrome must not take the CloakBrowser profile lock'))

        async def go():
            async with headless.session(self.root / 'state', pause_idle_worker=True, accept_downloads=True, browser_kind='chrome') as opened:
                self.assertIs(opened, browser)
        with patch.object(headless, 'profile_lock', cloak_lock), \
             patch.object(headless, 'ChromeBrowser', MagicMock(return_value=browser)), \
             patch.object(headless, 'load_config', MagicMock()):
            asyncio.run(go())
        browser.start.assert_awaited_once_with(headless=True, accept_downloads=True)
        browser.close.assert_awaited_once()

    def test_two_runs_cannot_share_the_chrome_profile(self):
        with headless.chrome_lock(self.root / 'state'):
            with self.assertRaises(headless.ResearchError) as error:
                with headless.chrome_lock(self.root / 'state'):
                    pass
        self.assertEqual(error.exception.code, 'profile_busy')
        with headless.chrome_lock(self.root / 'state'):                                 # released afterwards
            pass

    def test_the_default_session_is_still_cloakbrowser(self):
        import inspect
        self.assertEqual(inspect.signature(headless.session).parameters['browser_kind'].default, 'cloakbrowser')
        self.assertEqual(image_files.DEFAULT_BROWSER, 'cloakbrowser')


class ReceiptTests(Workspace):
    def receipt(self, **options):
        receipt = headless.Receipt(self.root / 'run' / 'receipt.json')
        receipt.create('run-1', 'a red fox', **options)
        return receipt

    def test_a_new_run_records_its_browser_and_defaults_to_cloakbrowser(self):
        self.assertEqual(self.receipt().data['browser'], 'cloakbrowser')

    def test_a_chrome_run_is_recorded_and_resumed_in_chrome_whatever_the_flags_say(self):
        receipt = self.receipt(browser='chrome')
        self.assertEqual(receipt.summary()['browser'], 'chrome')
        self.assertEqual(headless.browser_for(argparse.Namespace(browser=None), receipt), 'chrome')
        self.assertEqual(headless.browser_for(argparse.Namespace(browser='cloakbrowser'), receipt), 'chrome')

    def test_a_receipt_from_before_this_option_means_cloakbrowser(self):
        receipt = self.receipt()
        del receipt.data['browser']
        self.assertEqual(headless.browser_for(argparse.Namespace(browser='chrome'), receipt), 'cloakbrowser')

    def test_only_a_new_run_or_a_probe_chooses(self):
        self.assertEqual(headless.browser_for(argparse.Namespace(browser='chrome'), None), 'chrome')
        self.assertEqual(headless.browser_for(argparse.Namespace(), None), 'cloakbrowser')


class CommandLineTests(Workspace):
    def main(self, *argv):
        out = io.StringIO()
        with patch.object(sys, 'argv', ['headless.py', *argv]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                return headless.main(), out.getvalue()
            except SystemExit as exit_:
                return exit_.code, out.getvalue()

    def test_resume_and_status_refuse_a_browser_flag_because_the_receipt_decides(self):
        for command in ('resume', 'status'):
            code, _ = self.main(command, '--run-id', 'r1', '--browser', 'chrome', '--state-dir', str(self.root))
            self.assertEqual(code, 2)

    def test_login_picks_the_profile_of_the_chosen_browser(self):
        seen = []

        def fake(name):
            async def login(state, *urls):
                seen.append((name, state))
                return {'login_window_closed': True, 'browser': name}
            return login
        with patch.object(headless, 'login_cloak', fake('cloakbrowser')), patch.object(headless, 'login_chrome', fake('chrome')):
            self.assertEqual(self.main('login', '--state-dir', str(self.root))[0], 0)
            self.assertEqual(self.main('login', '--browser', 'chrome', '--state-dir', str(self.root))[0], 0)
        self.assertEqual(seen, [('cloakbrowser', self.root), ('chrome', self.root)])

    def test_login_opens_a_visible_window_waits_for_it_to_close_and_sends_nothing(self):
        events = []

        class Login:
            def __init__(self, state, config):
                pass

            async def open_login_page(self):
                events.append('opened')

            async def wait_until_closed(self):
                events.append('closed')

            async def close(self):
                events.append('released')
        with patch.object(headless, 'ChromeBrowser', Login), patch.object(headless, 'load_config', MagicMock()):
            code, out = self.main('login', '--browser', 'chrome', '--state-dir', str(self.root / 'state'))
        self.assertEqual(code, 0)
        self.assertEqual(events, ['opened', 'closed', 'released'])
        self.assertIn('"login_window_open": true', out)
        self.assertIn('No prompt will be sent', out)
        self.assertFalse((self.root / 'state' / 'headless-images').exists())          # no run, no receipt

    def test_package_run_accepts_the_option_too(self):
        seen = {}

        def spy(state, packages, download_dir, browser=None):
            seen['browser'] = browser
            raise headless.ResearchError('package_invalid', 'stop here')
        package = self.root / 'p'
        package.mkdir()
        with patch.object(package_run, 'create_runs', spy), contextlib.redirect_stdout(io.StringIO()):
            package_run.main(['--package', str(package), '--download-dir', str(self.root), '--state-dir', str(self.root / 's'), '--browser', 'chrome'])
        self.assertEqual(seen['browser'], 'chrome')


class FrontDoorTests(Workspace):
    def setUp(self):
        super().setUp()
        runtime = patch.object(imagine, 'reexec_in_runtime')
        runtime.start()
        self.addCleanup(runtime.stop)
        state = patch.object(headless, 'DEFAULT_STATE', self.root / 'state')
        state.start()
        self.addCleanup(state.stop)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = imagine.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_the_default_is_cloakbrowser_and_the_choice_list_matches_the_engine(self):
        self.assertEqual(imagine.parser().parse_args(['a fox']).browser, 'cloakbrowser')
        self.assertEqual(imagine.BROWSER_KINDS, image_files.BROWSER_KINDS)
        self.assertEqual(imagine.parser().parse_args(['a fox', '--browser', 'chrome']).browser, 'chrome')
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            imagine.parser().parse_args(['a fox', '--browser', 'firefox'])

    def test_a_dry_run_with_chrome_names_the_browser_and_sends_nothing(self):
        with patch.object(package_run, 'run_all', AsyncMock(side_effect=AssertionError('a dry run must not run anything'))):
            code, out, _ = self.run_main(['a fox', '--browser', 'chrome', '--dry-run', '--out', str(self.root / 'out')])
        self.assertEqual(code, 0)
        self.assertIn('in chrome', out)
        self.assertIn('nothing was sent', out)

    def test_offline_frontdoor_does_not_reexec_when_runtime_is_unavailable(self):
        real_import = builtins.__import__

        def missing_runtime(name, *args, **kwargs):
            if name == 'cloakbrowser':
                raise ImportError('fixture runtime unavailable')
            return real_import(name, *args, **kwargs)

        interpreter = self.root / 'fixture-python'
        interpreter.touch()
        with patch.object(builtins, '__import__', missing_runtime), \
             patch.object(imagine, 'VENV_PYTHON', interpreter), \
             patch.dict(os.environ), \
             patch.object(os, 'execv', side_effect=AssertionError('offline tests must not replace the process')) as reexec, \
             patch.object(package_run, 'run_all', AsyncMock(side_effect=AssertionError('a dry run must not run anything'))) as run:
            os.environ.pop('IMAGINE_REEXEC', None)
            code, out, _ = self.run_main(['a fox', '--dry-run', '--out', str(self.root / 'out')])
        self.assertEqual(code, 0)
        self.assertIn('nothing was sent', out)
        reexec.assert_not_called()
        run.assert_not_awaited()

    def test_a_missing_chrome_stops_before_a_dry_run_and_before_any_send(self):
        with patch.dict(os.environ, {'IHAV_WEB_IMAGEN_CHROME': str(self.root / 'nowhere')}), \
             patch.object(package_run, 'create_runs', MagicMock(side_effect=AssertionError('no receipt before the browser is known'))):
            with self.assertRaises(SystemExit) as error:
                self.run_main(['a fox', '--browser', 'chrome'])
        self.assertIn('Google Chrome was not found', str(error.exception))
        self.assertIn('Nothing was sent', str(error.exception))

    def test_a_chrome_run_is_recorded_as_chrome(self):
        seen = {}

        async def fake_run_all(namespace, receipts):
            seen['namespace'] = namespace
            seen['receipt_browser'] = receipts[0].data['browser']
            return {'runs': [{'run_id': receipts[0].data['run_id'], 'downloaded': True, 'send_state': 'confirmed',
                              'original_file': {'path': str(self.root / 'x.png'), 'dimensions': [1, 1]}}]}
        with patch.object(package_run, 'run_all', fake_run_all):
            code, out, _ = self.run_main(['a fox', '--browser', 'chrome', '--out', str(self.root / 'out')])
        self.assertEqual(code, 0)
        self.assertEqual(seen['receipt_browser'], 'chrome')
        self.assertEqual(seen['namespace'].browser, 'chrome')
        self.assertFalse(seen['namespace'].pause_idle_worker)


if __name__ == '__main__':
    unittest.main()
