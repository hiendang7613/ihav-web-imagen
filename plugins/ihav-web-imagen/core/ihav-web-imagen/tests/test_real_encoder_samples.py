"""Outputs of real encoders must never be rejected by the structural preflight (the false-rejection guard).

The samples are 24x16 px files written by Pillow 11.3.0 and libjpeg-turbo `cjpeg` (baseline, optimized, progressive, arithmetic,
restart intervals, grayscale, CMYK, EXIF/ICC, palette, 16-bit, animated PNG/WebP...). Each one must pass `validate_image_structure`
and decode in Chromium, so the preflight never rejects what the browser can use. Stricter checks need a damage argument, not a spec argument.
"""
import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/headless.py'
SPEC = importlib.util.spec_from_file_location('headless_real_samples', SCRIPT)
headless = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(headless)
import image_files

SAMPLES = json.loads((Path(__file__).with_name('real_encoder_samples.json')).read_text())['samples']


class RealEncoderSampleTests(unittest.IsolatedAsyncioTestCase):
    def test_every_real_encoder_output_passes_the_preflight(self):
        self.assertGreaterEqual(len(SAMPLES), 30)
        for name, sample in SAMPLES.items():
            with self.subTest(sample=name):
                image_files.validate_image_structure(base64.b64decode(sample['b64']), sample['mime'])

    def test_every_sample_is_still_rejected_when_cut_short(self):
        for name, sample in SAMPLES.items():
            raw = base64.b64decode(sample['b64'])
            for cut in (len(raw) - 1, len(raw) * 3 // 4, len(raw) // 2, 12):
                if sample['mime'] == 'image/jpeg' and cut == len(raw) - 1:
                    continue                                                  # a JPEG missing only the last EOI byte is checked elsewhere
                with self.subTest(sample=name, cut=cut), self.assertRaises(headless.ResearchError) as error:
                    image_files.validate_image_structure(raw[:cut], sample['mime'])
                self.assertEqual(error.exception.code, 'reference_image_invalid')

    async def test_every_sample_also_decodes_in_the_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = headless.Browser(root, headless.load_config(headless.DEFAULT_STATE))
            await browser.start(headless=True)
            try:
                page = await browser.context.new_page()
                (root / 'f.html').write_text('<main></main>')
                await page.goto((root / 'f.html').as_uri())
                for name, sample in SAMPLES.items():
                    with self.subTest(sample=name):
                        decoded = await image_files.decode_image(page, base64.b64decode(sample['b64']), sample['mime'])
                        self.assertGreater(decoded['width'], 0)
            finally:
                await browser.close()


if __name__ == '__main__':
    unittest.main()
