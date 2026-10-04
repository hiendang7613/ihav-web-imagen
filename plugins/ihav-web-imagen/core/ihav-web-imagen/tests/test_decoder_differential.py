"""The structural preflight against measured decoder verdicts (the evidence bar for every rule).

`decoder_differential.json` holds 69 rows (mutants of real encoder output and genuine files from a minimal encoder) with the verdicts of Chromium and Pillow recorded next to the
policy verdict (`expected`): reject when a mainstream decoder rejects, when picture data is missing, or (stated in `why`) for framing
sanity that no real file violates; accept when both decoders decode it. Adding a rule means adding the rows that justify it.
The Chromium column is re-measured on every run, so a browser update that changes a verdict fails here instead of drifting silently.
"""
import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/headless.py'
SPEC = importlib.util.spec_from_file_location('headless_differential', SCRIPT)
headless = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(headless)
import image_files

MUTANTS = json.loads(Path(__file__).with_name('decoder_differential.json').read_text())['mutants']


class DecoderDifferentialTests(unittest.IsolatedAsyncioTestCase):
    def test_preflight_matches_the_policy_on_every_mutant(self):
        self.assertGreaterEqual(len(MUTANTS), 69)
        for name, row in MUTANTS.items():
            with self.subTest(mutant=name, why=row['why']):
                raw = base64.b64decode(row['b64'])
                try:
                    image_files.validate_image_structure(raw, row['mime'])
                    verdict = 'accepts'
                except headless.ResearchError as error:
                    self.assertEqual(error.code, 'reference_image_invalid')
                    verdict = 'rejects'
                self.assertEqual(verdict, row['expected'])

    def test_a_rule_that_rejects_what_both_decoders_decode_must_say_why(self):
        for name, row in MUTANTS.items():
            if row['expected'] == 'rejects' and row['chromium'] == 'decodes' and row['pillow'] == 'decodes':
                self.assertTrue(row['why'].startswith(('framing sanity', 'picture data missing')), name)

    async def test_chromium_still_gives_the_recorded_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = headless.Browser(root, headless.load_config(headless.DEFAULT_STATE))
            await browser.start(headless=True)
            try:
                page = await browser.context.new_page()
                (root / 'f.html').write_text('<main></main>')
                await page.goto((root / 'f.html').as_uri())
                for name, row in MUTANTS.items():
                    with self.subTest(mutant=name):
                        try:
                            await image_files.decode_image(page, base64.b64decode(row['b64']), row['mime'])
                            verdict = 'decodes'
                        except headless.ResearchError:
                            verdict = 'rejects'
                        except Exception:
                            verdict = 'rejects'
                        self.assertEqual(verdict, row['chromium'])
            finally:
                await browser.close()


if __name__ == '__main__':
    unittest.main()
