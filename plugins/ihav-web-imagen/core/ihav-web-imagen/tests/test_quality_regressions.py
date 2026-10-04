import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/headless.py'
SPEC = importlib.util.spec_from_file_location('headless_quality_tests', SCRIPT)
headless = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(headless)
import image_files
import package_run

PNG = __import__('base64').b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')   # a real 1x1 PNG: the preflight now checks structure


class ReferenceValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_png_grammar_and_jpeg_without_a_scan_leave_no_package_receipt(self):
        duplicate_ihdr = PNG[:33] + PNG[8:33] + PNG[33:]
        jpeg_without_scan = bytes.fromhex('ffd8 ffc0 000b 08 0001 0001 01 01 11 00 ffd9')
        cases = (('duplicate_ihdr', 'png', duplicate_ihdr), ('jpeg_without_scan', 'jpg', jpeg_without_scan))
        for name, extension, raw in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                package = root / 'case'
                package.mkdir()
                (package / 'prompt.txt').write_text('Create one illustration using this reference')
                (package / f'reference.{extension}').write_bytes(raw)
                (package / 'package.json').write_text(json.dumps({'order': [f'reference.{extension}']}))
                with self.assertRaises(headless.ResearchError) as error:
                    package_run.create_runs(root / 'state', [package], root / 'out')
                self.assertEqual(error.exception.code, 'reference_image_invalid')
                self.assertFalse((root / 'state' / 'headless-images').exists())

    async def test_prepare_timeout_retains_the_failed_stage(self):
        with patch.object(headless,'library_page',AsyncMock(side_effect=TimeoutError('local timeout'))):
            with self.assertRaises(headless.ResearchError) as error:
                await headless.prepare(AsyncMock())
        self.assertEqual(error.exception.code,'composer_prepare_failed')
        self.assertEqual(error.exception.diagnostic,{'prepare':{'stage':'library_entry'}})

    def args(self, root, command, references):
        prompt=root/'prompt.txt'
        prompt.write_text('Create one illustration using both references')
        return argparse.Namespace(command=command,state_dir=root,run_id='validation',prompt_file=prompt,
                                  reference=references,download_file=None,requested_size=None,
                                  pause_idle_worker=False,preview=False,wait_seconds=0)

    async def test_bad_reference_paths_fail_before_receipt_or_browser(self):
        for kind in ('missing','directory','extension','duplicate','oversize'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                source=root/'source.png'
                source.write_bytes(PNG)
                refs=[source]
                if kind=='missing':refs=[root/'missing.png']
                if kind=='directory':source.unlink();source.mkdir()
                if kind=='extension':source.rename(root/'source.gif');refs=[root/'source.gif']
                if kind=='duplicate':refs=[source,source]
                if kind=='oversize':
                    with source.open('wb') as stream:stream.truncate(image_files.MAX_REFERENCE_BYTES+1)
                args=self.args(root,'generate',refs)
                with patch.object(headless,'execute') as execute, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(headless.run_command(args),2)
                    execute.assert_not_called()
                self.assertFalse((root/'headless-images/validation/receipt.json').exists())

    async def test_bad_probe_reference_does_not_acquire_shared_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            args=self.args(root,'probe',[root/'missing.png'])
            with patch.object(headless,'session') as session:
                with self.assertRaises(headless.ResearchError) as error:
                    await headless.execute(args,None)
                session.assert_not_called()
            self.assertEqual(error.exception.code,'reference_file_invalid')

    async def test_package_receipt_passes_headless_resume_reference_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            case=root/'case';case.mkdir()
            (case/'prompt.txt').write_text('Create one illustration using both references')
            (case/'source.png').write_bytes(PNG)
            (case/'guide.png').write_bytes(PNG)
            (case/'package.json').write_text(json.dumps({'order':['source.png','guide.png']}))
            receipt=package_run.create_runs(root,[case],root/'out')[0]
            self.assertEqual(receipt.data['required_reference_count'],2)
            self.assertEqual(receipt.data['reference_state'],'ready')
            self.assertEqual(len(receipt.data['references']),2)
            self.assertIn('download_stem',receipt.data)
            self.assertNotIn('download_file',receipt.data)
            args=self.args(root,'resume',[])
            args.run_id=receipt.data['run_id']
            session=AsyncMock()
            with patch.object(headless,'session',return_value=session) as factory, \
                    patch.object(headless,'prepare',AsyncMock(side_effect=headless.ResearchError('past_reference_preflight','Local sentinel'))):
                with self.assertRaises(headless.ResearchError) as error:
                    await headless.execute(args,receipt)
                self.assertEqual(error.exception.code,'past_reference_preflight')
                factory.assert_called_once()
                self.assertTrue(factory.call_args.kwargs['accept_downloads'])

    def test_independent_runner_calls_in_the_same_second_have_different_ids(self):
        with patch.object(package_run,'datetime') as clock:
            clock.now.return_value.strftime.return_value='20260930T141500'
            first=package_run.run_id_for('/tmp/same-case',set())
            second=package_run.run_id_for('/tmp/same-case',set())
        self.assertNotEqual(first,second)
        self.assertIsNotNone(headless.RUN_ID.fullmatch(first))
        self.assertIsNotNone(headless.RUN_ID.fullmatch(second))

    async def test_busy_runner_never_rewrites_the_active_owners_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt=headless.Receipt(Path(directory)/'receipt.json')
            receipt.create('active','Create one image')
            receipt.update(send_state='intent',send_clicks=1,downloaded=True,library_verified=True)
            before=receipt.path.read_bytes()
            with headless.run_lock(receipt.path.parent),patch.object(package_run,'flow',AsyncMock()) as flow:
                result=await package_run.run_one(AsyncMock(),receipt,wait_seconds=0,
                                                verify_library=True,slots=asyncio.Semaphore(1),stop=asyncio.Event())
                flow.assert_not_awaited()
            self.assertEqual(receipt.path.read_bytes(),before)
            self.assertEqual(result['error'],'run_busy')
            self.assertFalse(package_run.succeeded(result,True))

    async def test_busy_runner_reports_its_own_error_not_the_owners_old_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt=headless.Receipt(Path(directory)/'receipt.json')
            receipt.create('active','Create one image')
            receipt.update(error_kind='ui',reason='composer_not_ready')       # an older failure of this not-yet-sent run
            before=receipt.path.read_bytes()
            with headless.run_lock(receipt.path.parent),patch.object(package_run,'flow',AsyncMock()):
                result=await package_run.run_one(AsyncMock(),receipt,wait_seconds=0,
                                                verify_library=False,slots=asyncio.Semaphore(1),stop=asyncio.Event())
            self.assertEqual(receipt.path.read_bytes(),before)                # still output only
            self.assertEqual((result['error'],result['reason'],result['error_kind'],result['retry_safe']),
                             ('run_busy','run_busy','run_state',False))
            self.assertEqual(result['send_state'],'not_sent')                  # the stale verdict would have said retry-safe

    def test_new_download_goal_does_not_claim_the_previous_file_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            args=self.args(root,'resume',[])
            args.download_file=root/'new.png'
            receipt=headless.Receipt(root/'headless-images/validation/receipt.json')
            receipt.create('validation','Create one image')
            old=root/'old.png';old.write_bytes(b'old native artifact')
            original={'path':str(old),'sha256':'retained-identity'}
            receipt.update(send_state='confirmed',downloaded=True,download_file=str(old),original_file=original)
            with patch.object(headless,'execute',AsyncMock(side_effect=headless.ResearchError('stopped_before_browser','Local sentinel'))),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(headless.run_command(args),2)
            current=headless.Receipt(receipt.path)
            self.assertFalse(current.data['downloaded'])
            self.assertEqual(current.data['download_file'],str(args.download_file.resolve()))
            self.assertEqual(current.data['last_original_file'],original)
            self.assertEqual(current.data['original_file'],{})
            self.assertFalse(args.download_file.exists())
            self.assertEqual(old.read_bytes(),b'old native artifact')

    async def test_failed_resume_does_not_reuse_old_download_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            args=self.args(root,'resume',[])
            receipt=headless.Receipt(root/'headless-images/validation/receipt.json')
            receipt.create('validation','Create one image')
            receipt.update(send_state='confirmed',downloaded=True,library_verified=True,download_file=str(root/'old.png'))
            with patch.object(headless,'session',side_effect=headless.ResearchError('profile_busy','Local fixture')):
                with self.assertRaises(headless.ResearchError):await headless.execute(args,receipt)
            self.assertFalse(receipt.data['downloaded'])
            self.assertFalse(receipt.data['library_verified'])
            self.assertEqual(receipt.data['download_state'],'verification_pending')

    def test_upload_diagnostic_is_retained_in_receipt_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            args=self.args(root,'generate',[])
            error=headless.ResearchError('upload_not_ready','Local fixture upload is pending')
            error.diagnostic={'upload':{'tiles':[{'name':'source.png','ready':False}],'busy':True}}
            output=io.StringIO()
            with patch.object(headless,'execute',AsyncMock(side_effect=error)), contextlib.redirect_stdout(output):
                self.assertEqual(headless.run_command(args),2)
            summary=json.loads(output.getvalue())
            saved=json.loads((root/'headless-images/validation/receipt.json').read_text())
            self.assertEqual(summary['diagnostic'],error.diagnostic)
            self.assertEqual(saved['diagnostic'],error.diagnostic)
            self.assertEqual(saved['send_state'],'not_sent')
            self.assertEqual(saved['send_clicks'],0)


if __name__ == '__main__':
    unittest.main()
