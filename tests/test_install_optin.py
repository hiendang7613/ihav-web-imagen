"""Portable discovery must not invoke installed hosts without explicit opt-in."""
import importlib.util
import io
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).with_name('test_install_sh.py')
NATIVE_METHODS = (
    'test_install_and_reinstall_succeed_for_every_host_present',
    'test_a_bad_source_fails_loudly_with_the_command_to_run_by_hand',
)


def load_install_tests(value, host):
    spec = importlib.util.spec_from_file_location('offline_install_selection', SOURCE)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ), patch('shutil.which', return_value=host):
        os.environ.pop('IHAV_WEB_IMAGEN_HOST_TESTS', None)
        if value is not None:
            os.environ['IHAV_WEB_IMAGEN_HOST_TESTS'] = value
        spec.loader.exec_module(module)
    return module


class HostInstallerOptInTests(unittest.TestCase):
    def test_default_discovery_skips_real_installers_even_with_hosts_present(self):
        for value in (None, '', '0', 'true'):
            with self.subTest(value=value):
                module = load_install_tests(value, '/fixture/native-cli')
                suite = unittest.TestSuite(module.InstallShTests(name) for name in NATIVE_METHODS)
                with patch.object(subprocess, 'run', side_effect=AssertionError('native installer must not execute')) as invoke:
                    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
                self.assertEqual(result.testsRun, 2)
                self.assertEqual(len(result.skipped), 2)
                invoke.assert_not_called()

    def test_explicit_opt_in_selects_native_methods_only_when_a_host_exists(self):
        for host, selected in (('/fixture/native-cli', True), (None, False)):
            with self.subTest(host=host):
                module = load_install_tests('1', host)
                for name in NATIVE_METHODS:
                    method = getattr(module.InstallShTests, name)
                    self.assertEqual(not getattr(method, '__unittest_skip__', False), selected)
        # This control checks selection only; it never runs opted-in host commands.

    def test_system_path_host_is_detected_before_running_the_missing_host_fixture(self):
        module = load_install_tests(None, '/fixture/native-cli')
        suite = unittest.TestSuite([module.InstallShTests('test_without_a_host_cli_it_says_so_and_fails')])
        with patch('shutil.which', return_value='/fixture/native-cli'), \
             patch.object(subprocess, 'run', side_effect=AssertionError('system host must not execute')) as invoke:
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.skipped), 1)
        invoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
