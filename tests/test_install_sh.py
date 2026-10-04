"""install.sh end to end against throwaway homes. Real `claude` / `codex` installs run only where those CLIs exist (skipped in CI)."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'install.sh'


def run(home: Path, *, source=ROOT, path=None):
    env = {k: v for k, v in os.environ.items() if k not in {'CLAUDE_CONFIG_DIR', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'}}   # never reach the real config
    env.update({'HOME': str(home), 'CODEX_HOME': str(home / '.codex'), 'IHAV_WEB_IMAGEN_SOURCE': str(source)})
    if path is not None:
        env['PATH'] = path
    return subprocess.run(['sh', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=240)


class InstallShTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        (self.home / '.codex').mkdir()

    def test_the_script_is_valid_posix_sh_and_only_calls_the_hosts_own_plugin_commands(self):
        self.assertEqual(subprocess.run(['sh', '-n', str(SCRIPT)]).returncode, 0)
        text = SCRIPT.read_text()
        commands = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))   # comments may show the curl one-liner
        for forbidden in ('sudo', 'rm ', 'curl', 'wget', 'eval', 'chmod'):
            self.assertNotIn(forbidden, commands, forbidden)
        self.assertIn('plugin marketplace add', text)

    def test_default_source_is_this_checkout_even_when_called_from_another_directory(self):
        fake_bin = self.home / 'bin'
        fake_bin.mkdir()
        log = self.home / 'calls.log'
        fake_cli = fake_bin / 'claude'
        fake_cli.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$IHAV_WEB_IMAGEN_TEST_LOG"\n')
        fake_cli.chmod(0o755)
        spaced_checkout = self.home / 'checkout with spaces'
        spaced_checkout.mkdir()
        script = spaced_checkout / 'install.sh'
        shutil.copyfile(SCRIPT, script)
        (spaced_checkout / '.claude-plugin').mkdir()
        (spaced_checkout / '.claude-plugin/marketplace.json').write_text('{}')             # what makes a directory a checkout
        env = {k: v for k, v in os.environ.items() if k not in {'IHAV_WEB_IMAGEN_SOURCE', 'CLAUDE_CONFIG_DIR'}}
        env.update({'HOME': str(self.home), 'IHAV_WEB_IMAGEN_TEST_LOG': str(log), 'PATH': f'{fake_bin}:/usr/bin:/bin'})
        done = subprocess.run(['sh', str(script)], cwd=self.home, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = log.read_text().splitlines()
        self.assertTrue(calls[0].startswith(f'plugin marketplace add {spaced_checkout}'))
        self.assertTrue(any(call.startswith('plugin install ihav-web-imagen@ihav-web-imagen') for call in calls))
        self.assertTrue(any(call.startswith('plugin update ihav-web-imagen@ihav-web-imagen') for call in calls))   # an installed plugin keeps its cached version until updated

    def test_without_a_host_cli_it_says_so_and_fails(self):
        done = run(self.home, path='/usr/bin:/bin')
        if shutil.which('claude', path='/usr/bin:/bin') or shutil.which('codex', path='/usr/bin:/bin'):
            self.skipTest('a host CLI lives in a system directory here')
        self.assertEqual(done.returncode, 1)
        self.assertIn('Neither the claude nor the codex command was found', done.stdout)

    @unittest.skipUnless(shutil.which('claude') or shutil.which('codex'), 'no host CLI installed')
    def test_install_and_reinstall_succeed_for_every_host_present(self):
        for attempt in ('first run', 're-run (update)'):
            done = run(self.home)
            self.assertEqual(done.returncode, 0, f'{attempt}: {done.stdout}{done.stderr}')
            if shutil.which('claude'):
                self.assertIn('/ihav-web-imagen:imagine a red fox', done.stdout)
            if shutil.which('codex'):
                self.assertIn('$ihav-web-imagen:ihav-web-imagen a red fox', done.stdout)             # Codex lists a plugin's skill as plugin:skill

    def test_a_downloaded_copy_outside_a_checkout_uses_the_github_source(self):
        fake_bin = self.home / 'bin'
        fake_bin.mkdir()
        log = self.home / 'calls.log'
        cli = fake_bin / 'claude'
        cli.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$IHAV_WEB_IMAGEN_TEST_LOG"\n')
        cli.chmod(0o755)
        downloads = self.home / 'Downloads'
        downloads.mkdir()
        shutil.copyfile(SCRIPT, downloads / 'install.sh')                      # no marketplace file beside it: not a checkout
        env = {k: v for k, v in os.environ.items() if k not in {'IHAV_WEB_IMAGEN_SOURCE', 'CLAUDE_CONFIG_DIR'}}
        env.update({'HOME': str(self.home), 'IHAV_WEB_IMAGEN_TEST_LOG': str(log), 'PATH': f'{fake_bin}:/usr/bin:/bin'})
        done = subprocess.run(['sh', str(downloads / 'install.sh')], cwd=self.home, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(log.read_text().splitlines()[0], 'plugin marketplace add hiendang7613/ihav-web-imagen')

    @unittest.skipUnless(shutil.which('claude') or shutil.which('codex'), 'no host CLI installed')
    def test_a_bad_source_fails_loudly_with_the_command_to_run_by_hand(self):
        done = run(self.home, source=Path('/nonexistent/ihav-web-imagen'))
        self.assertEqual(done.returncode, 1)
        self.assertIn('failed. The last lines of what it said', done.stdout)
        self.assertIn('Run it yourself to see all of it', done.stdout)


if __name__ == '__main__':
    unittest.main()
