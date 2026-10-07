import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import sites  # noqa: E402

PAGE = '''<!doctype html><html><head><title>Fixture chat</title></head><body><main>
<form><div contenteditable="true" role="textbox" aria-label="Ask anything"></div>
<button aria-label="Add photos" data-testid="attach">+</button><input type="file" hidden>
<button aria-label="Send" data-testid="send">Send</button></form>
<a href="#">Log in</a><a href="#">Login</a><a href="#">Sign in</a>
<a href="#">Sign up</a><a href="#">Continue with Google</a>
<a href="#">Designing best eval tool</a><a href="#">Cataloging</a><a href="#">Assign input</a></main></body></html>'''


class SiteTests(unittest.TestCase):
    def test_all_means_every_site_and_unknown_names_are_refused(self):
        self.assertEqual(sites.resolve(['all']), list(sites.SITES))
        self.assertEqual(sites.resolve(None), list(sites.SITES))
        self.assertEqual(sites.resolve(['gemini', 'gemini', 'qwen']), ['gemini', 'qwen'])
        with self.assertRaises(ValueError):
            sites.resolve(['claude'])

    def test_only_surveyed_and_run_sites_have_adapters(self):
        self.assertEqual(sites.ADAPTERS, ('chatgpt',))
        self.assertTrue(set(sites.ADAPTERS) <= set(sites.SITES))
        self.assertTrue(all(url.startswith('https://') for _, urls in sites.SITES.values() for url in urls))


class LoginWiringTests(unittest.TestCase):
    def test_login_opens_one_tab_per_chosen_site(self):
        spec = importlib.util.spec_from_file_location('headless_sites', SCRIPTS / 'headless.py')
        headless = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(headless)
        seen = []

        async def fake(state, urls):
            seen.append(list(urls))
            return {'login_window_closed': True}
        with tempfile.TemporaryDirectory() as directory, patch.object(headless, 'login_cloak', fake), \
             patch.object(sys, 'argv', ['headless.py', 'login', '--state-dir', directory, '--site', 'gemini', '--site', 'zai']), \
             patch('sys.stdout'):
            self.assertEqual(headless.main(), 0)
        self.assertEqual(seen, [['https://gemini.google.com/app', 'https://chat.z.ai/', 'https://image.z.ai/']])


class SurveyTests(unittest.TestCase):
    def test_a_survey_reads_the_page_and_never_types_or_clicks(self):
        try:
            from chatgpt_web.core import load_config, DEFAULT_STATE
            from chatgpt_web.browser import cloak_binary
            cloak_binary(load_config(DEFAULT_STATE))
        except Exception as exc:  # no runtime here: the survey needs a browser
            self.skipTest(f'browser runtime not set up: {exc}')
        import survey
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'chat.html').write_text(PAGE)
            (root / 'config.json').write_text((DEFAULT_STATE / 'config.json').read_text())
            fixture = {'fixture': ('Fixture', ((root / 'chat.html').as_uri(),))}
            with patch.dict(sites.SITES, fixture), patch.object(survey, 'SETTLE_SECONDS', 0.2):
                results = asyncio.run(survey.survey(root, ['fixture'], root / 'out'))
            record = json.loads(Path(results[0]['file']).read_text())
        self.assertFalse(record['sent'])
        self.assertTrue(results[0]['composer_found'])
        self.assertTrue(results[0]['looks_signed_out'])
        self.assertEqual(record['inventory']['signin_signs'],
                         ['Log in', 'Login', 'Sign in', 'Sign up', 'Continue with Google'])
        labels = [b['aria_label'] for b in record['inventory']['composers'][0]['buttons']]
        self.assertEqual(labels, ['Add photos', 'Send'])
        self.assertEqual(len(record['inventory']['composers'][0]['file_inputs']), 1)


if __name__ == '__main__':
    unittest.main()


class DiffTests(unittest.TestCase):
    def write(self, root, stamp, labels, signed_out=False):
        import survey  # noqa: F401
        folder = root / 'site' / stamp
        folder.mkdir(parents=True)
        record = {'summary': {'looks_signed_out': signed_out, 'challenge': False},
                  'inventory': {'url': 'https://x.test/app', 'file_inputs': [],
                                'composers': [{'composer': {'tag': 'div', 'aria_label': 'Ask'}, 'file_inputs': [],
                                               'buttons': [{'tag': 'button', 'aria_label': label} for label in labels]}]}}
        (folder / 'survey.json').write_text(json.dumps(record))

    def test_a_renamed_send_button_and_a_lost_session_are_reported(self):
        import survey
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, '20261004T010000Z', ['Send prompt', 'Attach'])
            self.write(root, '20261004T020000Z', ['Send', 'Attach'], signed_out=True)
            report = survey.diff(root, 'site')
        self.assertEqual(report['changes']['composer_controls'], {'added': ['button|Send||'], 'removed': ['button|Send prompt||']})
        self.assertEqual(report['changes']['looks_signed_out'], {'before': False, 'after': True})

    def test_one_survey_cannot_be_compared_and_an_unchanged_page_has_no_changes(self):
        import survey
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, '20261004T010000Z', ['Send'])
            self.assertFalse(survey.diff(root, 'site')['compared'])
            self.write(root, '20261004T020000Z', ['Send'])
            self.assertEqual(survey.diff(root, 'site')['changes'], {})
