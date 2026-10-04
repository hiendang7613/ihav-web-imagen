import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
SPEC = importlib.util.spec_from_file_location('ihav_runtime', SCRIPTS / 'runtime.py')
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
from chatgpt_web import paths  # noqa: E402  (runtime.py put scripts/ on the path)


class StateFolderTests(unittest.TestCase):
    def test_the_override_wins_everywhere(self):
        with patch.dict(os.environ, {'IHAV_WEB_IMAGEN_HOME': '~/somewhere'}):
            self.assertEqual(paths.default_state(), Path('~/somewhere').expanduser())

    def test_platform_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('IHAV_WEB_IMAGEN_HOME', None)
            with patch.object(paths.sys, 'platform', 'darwin'):
                self.assertEqual(paths.default_state(), Path.home() / 'Library/Application Support/ihav-web-imagen')
            with patch.object(paths.sys, 'platform', 'linux'), patch.dict(os.environ, {'XDG_DATA_HOME': '/data'}):
                self.assertEqual(paths.default_state(), Path('/data/ihav-web-imagen'))


class DoctorTests(unittest.TestCase):
    def test_an_empty_state_is_not_ready_and_points_to_setup_without_creating_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            report = runtime.doctor(state)
            self.assertFalse(report['ready'])
            self.assertTrue(report['next'].endswith('runtime.py setup'))
            self.assertFalse(state.exists())

    def test_login_before_setup_stops_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit) as error:
                runtime.login(Path(directory), 'cloakbrowser')
            self.assertIn('Run setup first', str(error.exception))
            self.assertIn('Nothing was sent', str(error.exception))

    def test_doctor_prints_json(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(runtime.main(['doctor', '--state-dir', directory]), 0)
        self.assertEqual(json.loads(out.getvalue())['state_dir'], directory)


if __name__ == '__main__':
    unittest.main()
