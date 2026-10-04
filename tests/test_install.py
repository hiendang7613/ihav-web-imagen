"""Installer and plugin wrapper: stdlib only, runs anywhere (no browser, no ChatGPT)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = install.main([*argv])
    return code, out.getvalue()


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)

    def test_install_links_codex_writes_the_claude_wrapper_and_is_idempotent(self):
        code, out = run('--home', str(self.home), '--codex', '--claude')
        self.assertEqual(code, 0)
        link = self.home / '.codex/skills/ihav-web-imagen'
        self.assertTrue(link.is_symlink() and link.resolve() == install.SKILL.resolve())
        wrapper = self.home / '.claude/skills/ihav-web-imagen/SKILL.md'
        text = wrapper.read_text()
        self.assertIn('name: ihav-web-imagen', text)
        self.assertIn('disable-model-invocation: true', text)                 # it spends the user's account: never on the model's own initiative
        self.assertIn(f'python3 "{ROOT}/plugins/ihav-web-imagen/core/ihav-web-imagen/scripts/imagine.py"', text)
        self.assertNotIn('${CLAUDE_PLUGIN_ROOT}', text)
        self.assertIn('/ihav-web-imagen <what to draw>', text)
        self.assertNotIn('ihav-web-imagen:imagine', text)
        self.assertTrue(text.rstrip().endswith(install.MARKER))
        code, again = run('--home', str(self.home), '--codex', '--claude')
        self.assertEqual(code, 0)
        self.assertEqual(again.count('already installed'), 2)

    def test_by_default_it_installs_only_for_hosts_that_are_present(self):
        with patch.object(install.shutil, 'which', return_value=None):
            with self.assertRaises(SystemExit) as error:
                run('--home', str(self.home))                              # empty home, no CLI: nothing is created
            self.assertIn('Neither Codex nor Claude Code was found', str(error.exception))
            self.assertEqual(list(self.home.iterdir()), [])
            (self.home / '.claude').mkdir()
            run('--home', str(self.home))
        self.assertTrue((self.home / '.claude/skills/ihav-web-imagen/SKILL.md').is_file())
        self.assertFalse((self.home / '.codex').exists())

    def test_dry_run_changes_nothing(self):
        code, out = run('--home', str(self.home), '--codex', '--claude', '--dry-run')
        self.assertEqual((code, list(self.home.iterdir())), (0, []))
        self.assertIn('would link', out)
        self.assertIn('would write', out)

    def test_it_never_overwrites_what_it_did_not_create(self):
        (self.home / '.claude/skills/ihav-web-imagen').mkdir(parents=True)
        mine = self.home / '.claude/skills/ihav-web-imagen/SKILL.md'
        mine.write_text('someone else\'s skill')
        for flags in (['--claude'], ['--claude', '--force']):              # --force never replaces a file this installer did not write
            with self.subTest(flags=flags), self.assertRaises(SystemExit):
                run('--home', str(self.home), *flags)
            self.assertEqual(mine.read_text(), 'someone else\'s skill')
        (self.home / '.codex/skills/ihav-web-imagen').mkdir(parents=True)
        with self.assertRaises(SystemExit):
            run('--home', str(self.home), '--codex', '--force')              # --force replaces links only, never a real directory
        self.assertTrue((self.home / '.codex/skills/ihav-web-imagen').is_dir())

    def test_uninstall_removes_exactly_what_was_installed(self):
        run('--home', str(self.home), '--codex', '--claude')
        other = self.home / '.claude/skills/other/SKILL.md'
        other.parent.mkdir(parents=True)
        other.write_text('keep me')
        code, out = run('--home', str(self.home), '--uninstall')
        self.assertIn('removed', out)
        self.assertFalse((self.home / '.codex/skills/ihav-web-imagen').exists())
        self.assertFalse((self.home / '.claude/skills/ihav-web-imagen').exists())
        self.assertEqual(other.read_text(), 'keep me')
        self.assertIn('nothing of ours', run('--home', str(self.home), '--uninstall')[1])


class PluginTests(unittest.TestCase):
    def test_manifests_name_the_same_plugin_and_point_at_existing_paths(self):
        base = ROOT / 'plugins/ihav-web-imagen'
        claude = json.loads((base / '.claude-plugin/plugin.json').read_text())
        codex = json.loads((base / '.codex-plugin/plugin.json').read_text())
        claude_market = json.loads((ROOT / '.claude-plugin/marketplace.json').read_text())
        codex_market = json.loads((ROOT / '.agents/plugins/marketplace.json').read_text())
        self.assertEqual({claude['name'], codex['name'], claude_market['plugins'][0]['name'], codex_market['plugins'][0]['name']}, {'ihav-web-imagen'})
        self.assertEqual(claude['version'], codex['version'])                     # one version for both hosts
        self.assertEqual(claude_market['plugins'][0]['source'], './plugins/ihav-web-imagen')
        self.assertEqual(codex_market['plugins'][0]['source']['path'], './plugins/ihav-web-imagen')
        self.assertTrue((base / claude['skills'] / 'imagine/SKILL.md').is_file())   # Claude: the user-invoked wrapper
        self.assertTrue((base / codex['skills'] / 'ihav-web-imagen/SKILL.md').is_file())  # Codex: the universal skill
        self.assertTrue((ROOT / 'plugins/ihav-web-imagen/core/ihav-web-imagen/SKILL.md').is_file())

    def test_the_plugin_wrapper_is_the_template_the_installer_renders(self):
        text = install.TEMPLATE.read_text()
        head = text.split('---')[1]
        self.assertIn('name: imagine', head)
        self.assertIn('disable-model-invocation: true', head)
        self.assertIn('${CLAUDE_PLUGIN_ROOT}/core/ihav-web-imagen/scripts/imagine.py', text)
        self.assertTrue((ROOT / 'plugins/ihav-web-imagen/core/ihav-web-imagen/scripts/imagine.py').is_file())
        for flag in ('--ref', '--out', '--dry-run', '--wait-seconds', '--json', '--name'):
            self.assertIn(flag, text)

    def test_codex_runs_it_only_when_the_user_names_it(self):
        text = (ROOT / 'plugins/ihav-web-imagen/core/ihav-web-imagen/agents/openai.yaml').read_text()
        self.assertIn('allow_implicit_invocation: false', text)              # it spends the user's account: explicit $ihav-web-imagen only
        self.assertIn('$ihav-web-imagen', text)

    def test_nothing_private_is_in_the_repo_files_we_wrote(self):
        for path in [ROOT / 'install.py', ROOT / 'install.sh', install.TEMPLATE, *(ROOT / '.claude-plugin').glob('*.json'), ROOT / '.agents/plugins/marketplace.json']:
            text = path.read_text()
            for needle in ('/Users/', 'chatgpt-images-ui'):
                self.assertNotIn(needle, text, f'{needle} in {path.name}')


if __name__ == '__main__':
    unittest.main()
