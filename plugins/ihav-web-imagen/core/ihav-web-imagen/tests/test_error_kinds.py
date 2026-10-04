"""Failure classes and the one-meaning `retry_safe` (nothing was submitted, so a retry cannot duplicate)."""
import argparse
import asyncio
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import headless
import package_run


def codes_in_source():
    """Every error code the scripts can raise: ResearchError('code', ...) and required(locator, 'code')."""
    found = set()
    for path in SCRIPTS.glob('*.py'):
        text = path.read_text()
        found |= set(re.findall(r"ResearchError\(\s*'([a-z][a-z_0-9]+)'", text))
        found |= set(re.findall(r"required\([^\n]*?,\s*'([a-z][a-z_0-9]+)'", text))
    return found


class ClassificationTests(unittest.TestCase):
    def test_every_code_the_scripts_raise_has_a_kind_so_new_codes_cannot_slip_by_unclassified(self):
        codes = codes_in_source()
        self.assertGreater(len(codes), 50)
        unclassified = sorted(c for c in codes if headless.classify_error(c) == 'other')
        self.assertEqual(unclassified, [], 'classify_error() needs a kind for these new codes')

    def test_representative_codes(self):
        expected = {'send_click_uncertain': 'ambiguous_send', 'login_required': 'site_state', 'chatgpt_alert': 'site_state',
                    'profile_busy': 'busy', 'research_active': 'busy', 'run_exists_use_resume': 'run_state',
                    'run_busy': 'run_state', 'reference_file_invalid': 'input', 'invalid_prompt': 'input',
                    'download_failed': 'download', 'browser_binary_missing': 'environment',
                    'composer_unavailable': 'ui', 'send_unavailable': 'ui', 'upload_not_ready': 'ui',
                    'reference_order_unverified': 'ui', 'effort_restore_failed': 'ui', 'TimeoutError': 'other'}
        for code, kind in expected.items():
            self.assertEqual(headless.classify_error(code), kind, code)

    def test_retry_safe_means_nothing_was_submitted(self):
        safe = {'send_state': 'not_sent', 'send_clicks': 0}
        self.assertTrue(headless.retry_safe(safe, 'ui'))
        self.assertTrue(headless.retry_safe(None, 'input'))                       # no receipt: nothing was ever started
        for state in ('intent', 'unknown', 'confirmed'):
            self.assertFalse(headless.retry_safe({'send_state': state, 'send_clicks': 1}, 'ui'), state)
        self.assertFalse(headless.retry_safe({'send_state': 'not_sent', 'send_clicks': 1}, 'ui'))
        for kind in ('ambiguous_send', 'run_state'):
            self.assertFalse(headless.retry_safe(safe, kind), kind)              # resume/status, never a new request


class FailurePathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def generate(self, raising):
        prompt = self.root / 'prompt.txt'
        prompt.write_text('Create one image')
        args = argparse.Namespace(command='generate', state_dir=self.root, run_id='r1', prompt_file=prompt, reference=[],
                                  download_file=None, requested_size=None, pause_idle_worker=False, preview=False,
                                  wait_seconds=0, effort=None)
        out = io.StringIO()
        with patch.object(headless, 'execute', raising), contextlib.redirect_stdout(out):
            code = headless.run_command(args)
        return code, json.loads(out.getvalue()), headless.Receipt(self.root / 'headless-images/r1/receipt.json')

    def test_a_failure_before_send_is_classified_and_retry_safe(self):
        code, printed, receipt = self.generate(AsyncMock(side_effect=headless.ResearchError('composer_unavailable', 'x')))
        self.assertEqual(code, 2)
        self.assertEqual((printed['error'], printed['error_kind'], printed['retry_safe']), ('composer_unavailable', 'ui', True))
        self.assertEqual((receipt.data['error_kind'], receipt.summary()['retry_safe']), ('ui', True))

    def test_a_failure_after_the_send_intent_is_never_retry_safe(self):
        async def fail_after_intent(args, receipt):
            receipt.update(send_state='intent', send_clicks=1)
            raise headless.ResearchError('send_click_uncertain', 'lost')
        code, printed, receipt = self.generate(fail_after_intent)
        self.assertEqual((printed['error_kind'], printed['retry_safe'], printed['send_state']), ('ambiguous_send', False, 'unknown'))

    def test_a_non_research_exception_is_other_and_a_duplicate_generate_points_to_resume(self):
        code, printed, _ = self.generate(AsyncMock(side_effect=TimeoutError('t')))
        self.assertEqual((printed['error'], printed['error_kind'], printed['retry_safe']), ('TimeoutError', 'other', True))
        # the same run id again: the existing run is never re-sent; the answer says so and keeps the earlier receipt
        prompt = self.root / 'prompt.txt'
        args = argparse.Namespace(command='generate', state_dir=self.root, run_id='r1', prompt_file=prompt, reference=[],
                                  download_file=None, requested_size=None, pause_idle_worker=False, preview=False, wait_seconds=0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            headless.run_command(args)
        again = json.loads(out.getvalue())
        self.assertEqual((again['error'], again['error_kind'], again['retry_safe']), ('run_exists_use_resume', 'run_state', False))

    def test_probe_failures_are_retry_safe_because_a_probe_never_sends(self):
        args = argparse.Namespace(command='probe', state_dir=self.root, pause_idle_worker=False, reference=[])
        out = io.StringIO()
        with patch.object(headless, 'execute', AsyncMock(side_effect=headless.ResearchError('login_required', 'x'))), \
             contextlib.redirect_stdout(out):
            headless.run_command(args)
        printed = json.loads(out.getvalue())
        self.assertEqual((printed['error_kind'], printed['retry_safe']), ('site_state', True))

    def test_a_resumed_run_does_not_keep_the_old_failure_kind(self):
        _, _, receipt = self.generate(AsyncMock(side_effect=headless.ResearchError('composer_unavailable', 'x')))
        receipt.update(send_state='confirmed', send_clicks=1, conversation_url='https://chatgpt.com/c/owned')
        page = MagicMock(url='https://chatgpt.com/c/owned', goto=AsyncMock())
        browser = MagicMock()
        browser.context.new_page = AsyncMock(return_value=page)
        browser.check_access = AsyncMock()

        @contextlib.asynccontextmanager
        async def session(*_, **__):
            yield browser
        args = argparse.Namespace(command='resume', state_dir=self.root, pause_idle_worker=False, wait_seconds=0, preview=False)
        with patch.object(headless, 'session', session), patch.object(headless, 'required', AsyncMock()), \
             patch.object(headless, 'observe', AsyncMock(return_value=False)), patch.object(headless, 'verify_library', AsyncMock()):
            summary = asyncio.run(headless.execute(args, receipt))
        self.assertIsNone(receipt.data.get('error_kind'))
        self.assertFalse(summary['retry_safe'])


class PackageRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_records_the_kind_and_retry_verdict_per_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'case'
            folder.mkdir()
            (folder / 'prompt.txt').write_text('Create one image')
            (folder / 'package.json').write_text(json.dumps({'order': []}))
            (receipt,) = package_run.create_runs(root / 'state', [folder], root / 'out')
            with patch.object(package_run, 'flow', AsyncMock(side_effect=headless.ResearchError('upload_not_ready', 'slow'))):
                summary = await package_run.run_one(MagicMock(), receipt, wait_seconds=1, verify_library=False,
                                                    slots=asyncio.Semaphore(1), stop=asyncio.Event())
            self.assertEqual((summary['reason'], summary['error_kind'], summary['retry_safe']), ('upload_not_ready', 'ui', True))
            with patch.object(package_run, 'create_runs', side_effect=headless.ResearchError('package_invalid', 'bad')), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(package_run.main(['--package', 'x', '--download-dir', 'y']), 2)
            printed = json.loads(out.getvalue())
            self.assertEqual((printed['error_kind'], printed['retry_safe']), ('input', True))


if __name__ == '__main__':
    unittest.main()
