"""The one-line front door `imagine.py "<prompt>"`: package building, the dry run, the outcome report, and the never-retry rule.

No browser, no profile and no ChatGPT request: `package_run.run_all` is mocked wherever a run would start.
"""
import argparse
import asyncio
import base64
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import imagine
import package_run
from package_run import headless

PIXEL = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = imagine.main(argv)
    return code, out.getvalue(), err.getvalue()


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def image(self, name='photo.png'):
        path = self.root / name
        path.write_bytes(PIXEL)
        return str(path)

    def test_slug_is_a_short_safe_stem(self):
        self.assertEqual(imagine.slug('A beautiful girl!'), 'a-beautiful-girl')
        self.assertEqual(imagine.slug('???'), 'image')
        self.assertLessEqual(len(imagine.slug('word ' * 40)), 40)
        self.assertNotIn('/', imagine.slug('../../etc/passwd'))

    def test_a_package_holds_the_prompt_exactly_and_the_references_in_order_under_distinct_names(self):
        folder = imagine.build_package('  a beautiful girl  ', [self.image('Photo One.png'), self.image('palette.PNG')], self.root / 'pkg')
        self.assertEqual((folder / 'prompt.txt').read_text(), 'a beautiful girl\n')
        order = json.loads((folder / 'package.json').read_text())['order']
        self.assertEqual(order, ['1_Photo-One.png', '2_palette.png'])
        self.assertTrue(all((folder / name).read_bytes() == PIXEL for name in order))
        prompt, paths = package_run.load_package(folder)                      # the runner reads it back as written
        self.assertEqual((prompt, [p.name for p in paths]), ('a beautiful girl', order))

    def test_bad_input_is_refused_before_anything_exists_to_send(self):
        for case, arguments in {'empty': ('   ', []), 'too long': ('x' * 16001, []), 'three references': ('x', [self.image('a.png')] * 3),
                                'not an image': ('x', [str(self.root / 'notes.txt')]), 'missing file': ('x', [str(self.root / 'nope.png')])}.items():
            (self.root / 'notes.txt').write_text('hello')
            with self.subTest(case=case), self.assertRaises(SystemExit) as error:
                imagine.build_package(*arguments, self.root / f'pkg-{case.replace(" ", "-")}')
            self.assertIn('Nothing was sent', str(error.exception))


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = patch.object(headless, 'DEFAULT_STATE', self.root / 'real-state')
        self.state.start()
        self.addCleanup(self.state.stop)

    def test_dry_run_validates_without_a_browser_a_send_or_a_receipt_in_the_real_state(self):
        reference = self.root / 'ref.png'
        reference.write_bytes(PIXEL)
        with patch.object(package_run, 'run_all', AsyncMock(side_effect=AssertionError('a dry run must not run anything'))):
            code, out, err = run_main(['a', 'beautiful', 'girl', '--ref', str(reference), '--out', str(self.root / 'out'), '--dry-run'])
        self.assertEqual(code, 0)
        self.assertIn('Dry run: the package is valid', out)
        self.assertIn('nothing was sent, no browser started', out)
        self.assertFalse((self.root / 'real-state').exists())                      # not even the state directory
        self.assertFalse((self.root / 'out').exists())

    def test_dry_run_reports_a_damaged_reference_as_not_sent_and_safe_to_fix(self):
        damaged = bytearray(PIXEL)
        damaged[len(damaged) // 2] ^= 0xFF
        reference = self.root / 'bad.png'
        reference.write_bytes(bytes(damaged))
        code, out, err = run_main(['a girl', '--ref', str(reference), '--dry-run'])
        self.assertEqual(code, 2)
        self.assertIn('reference_image_invalid', out)
        self.assertIn('Nothing was sent', out)


class RunTests(unittest.TestCase):
    """The real path with `run_all` mocked: one run, never a second."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = patch.object(headless, 'DEFAULT_STATE', self.root / 'state')
        self.state.start()
        self.addCleanup(self.state.stop)

    def receipts(self):
        return [headless.Receipt(path) for path in sorted((self.root / 'state/headless-images').glob('*/receipt.json'))]

    def test_success_prints_where_the_file_is_and_runs_exactly_once(self):
        async def finished(args, receipts):
            receipt = receipts[0]
            receipt.update(send_state='confirmed', send_clicks=1, downloaded=True, generation_state='completed',
                           original_file={'path': str(self.root / 'out/a-girl.png'), 'dimensions': [1024, 1024]},
                           phase_seconds={'start': 0.0, 'intent': 15.0, 'final_complete': 61.4})
            return {'runs': [receipt.summary()]}
        mocked = AsyncMock(side_effect=finished)
        with patch.object(package_run, 'run_all', mocked):
            code, out, err = run_main(['a girl', '--out', str(self.root / 'out')])
        self.assertEqual(code, 0)
        self.assertIn(f'Image saved: {self.root / "out/a-girl.png"}', out)
        self.assertIn('1 Send, generation 46 s', out)
        self.assertIn('  1024x1024  run ', out)                                  # `[1024, 1024]` is shown as 1024x1024
        self.assertEqual(mocked.await_count, 1)
        namespace = mocked.await_args.args[0]
        self.assertEqual((namespace.concurrency, namespace.pause_idle_worker, namespace.effort, namespace.verify_library), (1, False, None, False))
        self.assertEqual(len(self.receipts()), 1)

    def test_a_failure_after_the_send_says_do_not_rerun_and_names_the_recovery_command(self):
        async def broken(args, receipts):
            receipts[0].update(send_state='confirmed', send_clicks=1)
            raise TimeoutError('browser teardown hung')
        mocked = AsyncMock(side_effect=broken)
        with patch.object(package_run, 'run_all', mocked):
            code, out, err = run_main(['a girl', '--out', str(self.root / 'out')])
        self.assertEqual(code, 2)
        self.assertIn('Do NOT run this again', out)
        run_id = self.receipts()[0].data['run_id']
        self.assertIn(f'headless.py resume --run-id {run_id}', out)
        self.assertNotIn('is safe', out)
        self.assertEqual(mocked.await_count, 1)                                    # no automatic second attempt

    def test_a_failure_before_any_send_says_a_rerun_is_safe(self):
        mocked = AsyncMock(side_effect=headless.ResearchError('profile_busy', 'another process owns the profile'))
        with patch.object(package_run, 'run_all', mocked):
            code, out, err = run_main(['a girl', '--out', str(self.root / 'out'), '--json'])
        self.assertEqual(code, 2)
        self.assertIn('profile_busy', out)
        self.assertIn('running the same command again after fixing the cause is safe', out)
        self.assertEqual(json.loads(out[out.index('{'):])['retry_safe'], True)

    def test_a_run_that_ends_unknown_is_never_called_safe(self):
        async def unknown(args, receipts):
            receipts[0].update(send_state='unknown', send_clicks=1, reason='posted_prompt_unverified')
            return {'runs': [receipts[0].summary()]}
        with patch.object(package_run, 'run_all', AsyncMock(side_effect=unknown)):
            code, out, err = run_main(['a girl', '--out', str(self.root / 'out')])
        self.assertEqual(code, 2)
        self.assertIn('send_state=unknown', out)
        self.assertIn('Do NOT run this again', out)

    def test_options_reach_the_runner(self):
        async def finished(args, receipts):
            return {'runs': [{'run_id': 'x', 'send_state': 'not_sent', 'reason': 'x'}]}
        mocked = AsyncMock(side_effect=finished)
        with patch.object(package_run, 'run_all', mocked):
            run_main(['a girl', '--wait-seconds', '90', '--verify-library', '--out', str(self.root / 'out')])
        namespace = mocked.await_args.args[0]
        self.assertEqual((namespace.wait_seconds, namespace.verify_library), (90, True))
        with self.assertRaises(SystemExit):
            run_main(['a girl', '--wait-seconds', '5000'])


class RuntimeHandoverTests(unittest.TestCase):
    def test_a_system_python_hands_over_to_the_runtime_venv_once(self):
        with patch.dict(sys.modules, {'cloakbrowser': None}), patch.object(imagine.os, 'execv') as execv, \
             patch.dict(imagine.os.environ, {}, clear=False):
            imagine.os.environ.pop('IMAGINE_REEXEC', None)
            execv.side_effect = RuntimeError('exec replaces the process: nothing after it runs')
            with self.assertRaises(RuntimeError):
                imagine.reexec_in_runtime()
            command = execv.call_args.args[1]
            self.assertEqual((command[0], Path(command[1]).name), (str(imagine.VENV_PYTHON), 'imagine.py'))
            self.assertEqual(imagine.os.environ['IMAGINE_REEXEC'], '1')
            execv.reset_mock()
            with self.assertRaises(SystemExit) as error:                          # already handed over: no loop, a clear message
                imagine.reexec_in_runtime()
            execv.assert_not_called()
            self.assertIn('Nothing was sent', str(error.exception))

    def test_nothing_happens_when_the_runtime_is_already_importable(self):
        with patch.dict(sys.modules, {'cloakbrowser': object()}), patch.object(imagine.os, 'execv') as execv:
            imagine.reexec_in_runtime()
            execv.assert_not_called()


class SkillDocumentTests(unittest.TestCase):
    def test_skill_md_documents_the_one_line_command_and_every_option(self):
        text = (SCRIPTS.parent / 'SKILL.md').read_text()
        self.assertIn('$ihav-web-imagen a beautiful girl', text)
        self.assertIn('scripts/imagine.py', text)
        for action in imagine.parser()._actions:
            for flag in action.option_strings:
                if flag.startswith('--') and flag != '--help':
                    self.assertIn(flag, text, f'{flag} is not documented in SKILL.md')
        self.assertTrue(text.startswith('---\nname: ihav-web-imagen\n'))


class DefaultFolderTests(unittest.TestCase):
    """The file goes to the terminal's current directory unless `--out` says otherwise."""

    def test_without_out_the_runner_gets_the_current_directory_and_out_overrides_it(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'here').mkdir()
            (root / 'chosen').mkdir()
            seen = []

            def spy(state, packages, download_dir, browser=None):
                seen.append(Path(download_dir).resolve())
                raise headless.ResearchError('package_invalid', 'stop here: only the folder is under test')
            before = Path.cwd()
            os.chdir(root / 'here')
            try:
                with patch.object(package_run, 'create_runs', spy):
                    run_main(['a girl'])
                    run_main(['a girl', '--out', str(root / 'chosen')])
            finally:
                os.chdir(before)
            self.assertEqual(seen, [(root / 'here').resolve(), (root / 'chosen').resolve()])

    def test_help_says_current_directory(self):
        self.assertIn('current directory', ' '.join(imagine.parser().format_help().split()))      # help wraps lines


class SizeTests(unittest.TestCase):
    def test_size_reads_like_a_resolution(self):
        self.assertEqual(imagine.size([1122, 1402]), '1122x1402')
        self.assertEqual(imagine.size((64, 48)), '64x48')
        self.assertEqual(imagine.size(None), 'None')
        self.assertEqual(imagine.size('odd'), 'odd')


class ReviewFixesTests(unittest.TestCase):
    """Findings of the independent docs-claims review (2026-10-01)."""

    def test_the_recovery_command_names_an_interpreter_that_can_import_the_runtime(self):
        import shlex
        import subprocess
        lines, code = imagine.report({'runs': [{'run_id': 'run-1', 'send_state': 'unknown', 'reason': 'posted_prompt_unverified'}]}, 2)
        command = next(line for line in lines if 'headless.py' in line).split(': ', 1)[1]
        parts = shlex.split(command)
        self.assertEqual((parts[0], Path(parts[1]).name, parts[2:]), (sys.executable, 'headless.py', ['resume', '--run-id', 'run-1']))
        done = subprocess.run([parts[0], '-c', f'import sys; sys.path.insert(0, {str(SCRIPTS)!r}); import headless'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)               # `python3` on PATH is not necessarily the runtime: this one is

    def test_a_skill_path_with_spaces_stays_one_argument_in_the_recovery_command(self):
        import shlex
        with patch.object(imagine, 'SKILL_SCRIPTS', Path('/tmp/a folder/scripts')):
            lines, code = imagine.report({'runs': [{'run_id': 'r', 'send_state': 'unknown'}]}, 2)
        command = next(line for line in lines if 'headless.py' in line).split(': ', 1)[1]
        self.assertEqual(shlex.split(command)[1], '/tmp/a folder/scripts/headless.py')

    def test_a_run_stopped_before_its_intent_says_nothing_was_sent(self):
        # 2026-10-04 real run: the library page had changed, so it stopped before the composer; send_clicks 0.
        run = {'run_id': 'r', 'send_state': 'not_sent', 'send_clicks': 0, 'reason': 'library_unavailable'}
        lines, code = imagine.report({'runs': [run], 'retry_safe': False}, 2)
        self.assertEqual(code, 2)
        self.assertIn('Nothing was sent', '\n'.join(lines))
        self.assertNotIn('Do NOT run this again', '\n'.join(lines))

    def test_a_saved_image_is_never_reported_as_no_image_saved(self):
        run = {'run_id': 'r', 'send_state': 'confirmed', 'downloaded': True, 'library_verified': False, 'reason': 'library_identity_unverified',
               'original_file': {'path': '/work/fox.png', 'dimensions': [64, 48]}}
        lines, code = imagine.report({'runs': [run]}, 2)               # package_run exits 2 when --verify-library did not pass
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], 'Image saved: /work/fox.png')
        self.assertTrue(any('Library check did not pass' in line and 'Do not run this again' in line for line in lines))
        self.assertFalse(any('No image saved' in line for line in lines))

    def test_an_unwritable_output_folder_stops_before_anything_is_sent(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'afile').write_text('x')
            for case, out in {'parent is a file': root / 'afile' / 'sub'}.items():
                with self.subTest(case=case), self.assertRaises(SystemExit) as error:
                    imagine.check_folder(out)
                self.assertIn('Nothing was sent', str(error.exception))
            imagine.check_folder(root / 'new' / 'deeper')                   # creatable below a writable folder: fine
            if os.geteuid() != 0:
                (root / 'locked').mkdir()
                (root / 'locked').chmod(0o500)
                try:
                    with self.assertRaises(SystemExit):
                        imagine.check_folder(root / 'locked' / 'sub')
                finally:
                    (root / 'locked').chmod(0o700)

    def test_dry_run_also_checks_the_output_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / 'afile'
            blocker.write_text('x')
            with self.assertRaises(SystemExit):
                run_main(['a fox', '--dry-run', '--out', str(blocker / 'sub')])

    def test_the_description_can_come_from_stdin_exactly_as_typed(self):
        text = 'SALE $50 off, `backtick`, "quotes", back\\slash'
        seen = []

        def spy(state, packages, download_dir, browser=None):
            seen.append((Path(packages[0]) / 'prompt.txt').read_text().strip())
            raise headless.ResearchError('package_invalid', 'stop here')
        with patch.object(sys, 'stdin', io.StringIO(text + '\n')), patch.object(package_run, 'create_runs', spy):
            run_main(['-'])
        self.assertEqual(seen, [text])

    def test_ctrl_c_after_the_click_ends_unknown_and_says_do_not_rerun(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(headless, 'DEFAULT_STATE', Path(directory) / 'state'):
            async def interrupted(args, receipts):
                receipts[0].update(send_state='intent', send_clicks=1)
                raise KeyboardInterrupt()
            with patch.object(package_run, 'run_all', AsyncMock(side_effect=interrupted)):
                code, out, err = run_main(['a girl', '--out', directory])
            self.assertEqual(code, 2)
            self.assertIn('Do NOT run this again', out)
            saved = headless.Receipt(next(Path(directory).glob('state/headless-images/*/receipt.json'))).data
            self.assertEqual(saved['send_state'], 'unknown')

    def test_the_run_id_is_printed_before_the_wait_so_an_interrupted_run_can_be_resumed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(headless, 'DEFAULT_STATE', Path(directory) / 'state'):
            async def finished(args, receipts):
                return {'runs': [{'run_id': receipts[0].data['run_id'], 'send_state': 'not_sent', 'reason': 'x'}]}
            with patch.object(package_run, 'run_all', AsyncMock(side_effect=finished)):
                code, out, err = run_main(['a girl', '--out', directory])
            run_id = headless.Receipt(next(Path(directory).glob('state/headless-images/*/receipt.json'))).data['run_id']
            self.assertIn(f'run {run_id}', err)

    def test_the_default_wait_leaves_room_under_a_ten_minute_host_limit(self):
        self.assertEqual(imagine.parser().parse_args(['x']).wait_seconds, 480)


class ProgressTests(unittest.TestCase):
    def test_runner_events_become_short_bullets_and_other_lines_are_dropped(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            writer = imagine.Progress()
            writer.write('{"event": "submission_confirmed", "send_state": "confirmed"}\nnot json\n{"event": "other"}\n')
        self.assertEqual(err.getvalue(), '  - Send confirmed, waiting for the image\n')


if __name__ == '__main__':
    unittest.main()
