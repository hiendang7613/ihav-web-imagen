"""Regression tests for turn binding, uploads, native downloads and run measurements.

Local file:// fixtures, temporary profile, no ChatGPT. The long-prompt turn mirrors the sanitised live markup read
read-only on 2026-09-30 (bubble text = whole prompt + "…" line + "Show more"; attachments outside the bubble).
"""
import argparse
import base64
import importlib.util
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/headless.py'
SPEC = importlib.util.spec_from_file_location('headless_writer_fix_tests', SCRIPT)
headless = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(headless)
import image_files
import package_run

PIXEL = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')
LONG_PROMPT = 'Edit image 2 using the layout of image 1, keep every word of the text exactly.\nSecond line. ' * 60


def turn_html(bubble_inner):
    return f'''<div data-turn-key="turn-0"><div><div data-chatgpt-search-unit-key="fallback-turn-0:0:user">
<div><div role="button" aria-label="User attachment"><img alt="1_source.png" width="40" height="40"></div>
<div role="button" aria-label="User attachment"><img alt="2_guide.png" width="40" height="40"></div></div>
<div data-content-search-unit-key="fallback-turn-0:0:user"><div>
<div data-user-message-bubble="true">{bubble_inner}</div></div></div></div></div>
<div data-testid="generated-image-gallery"><button data-testid="generated-image-preview" aria-label="Generated image 1"><img id="result" alt="Generated image 1"></button></div></div>'''


def collapsed(prompt):
    return (f'<div class="flex flex-col items-end gap-1"><div class="relative w-full" style="white-space:pre-wrap">{prompt}'
            '<span class="block">…</span></div><button data-thread-find-skip="true">Show more</button></div>')


def expanded(prompt):
    return (f'<div class="flex flex-col items-end gap-1"><div class="relative w-full" style="white-space:pre-wrap">{prompt}</div>'
            '<button data-thread-find-skip="true">Show less</button></div>')


class BrowserCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.browser = headless.Browser(self.root, headless.load_config(headless.DEFAULT_STATE))
        await self.browser.start(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.context.new_page()

    async def html(self, content):
        fixture = self.root / 'fixture.html'
        fixture.write_text(content)
        await self.page.goto(fixture.as_uri(), wait_until='domcontentloaded')

    async def paint_result(self):
        await self.page.evaluate('''() => {const c=document.createElement('canvas');c.width=128;c.height=128;
          const x=c.getContext('2d');x.fillStyle='green';x.fillRect(0,0,128,128);document.getElementById('result').src=c.toDataURL();}''')
        await self.page.wait_for_function('() => {const e=document.getElementById("result");return e.complete&&e.naturalWidth>0}')

    async def view(self, prompt=LONG_PROMPT):
        return await self.page.evaluate(headless.TURN_STATE, {'prompt': prompt, 'stop': headless.STOP})


class TurnBindingTests(BrowserCase):
    async def test_long_prompt_collapsed_by_show_more_is_still_the_owned_turn(self):
        await self.html(turn_html(collapsed(LONG_PROMPT)))
        await self.paint_result()
        view = await self.view()
        self.assertEqual((view['matches'], view['user_count'], view['turn_id']), (1, 1, 'turn-0'))
        self.assertEqual([[i['width'], i['height']] for i in view['images']], [[128, 128]])
        fingerprints = await self.page.evaluate(headless.TURN_FINGERPRINTS, {'prompt': LONG_PROMPT})
        self.assertEqual(len(fingerprints), 1)
        self.assertIsNotNone(fingerprints[0]['pixel_sha256'])

    async def test_expanded_bubble_with_show_less_matches_too(self):
        await self.html(turn_html(expanded(LONG_PROMPT)))
        await self.paint_result()
        self.assertEqual((await self.view())['matches'], 1)

    async def test_different_prompt_with_the_same_toggle_does_not_match(self):
        await self.html(turn_html(collapsed('A different request. ' * 80)))
        await self.paint_result()
        view = await self.view()
        self.assertEqual((view['matches'], view['user_count'], view['images']), (0, 1, []))

    async def test_prefix_of_a_longer_bubble_does_not_match(self):
        await self.html(turn_html(collapsed(LONG_PROMPT + ' And also delete everything.')))
        await self.paint_result()
        self.assertEqual((await self.view())['matches'], 0)

    async def test_words_show_more_without_a_toggle_button_are_prompt_text(self):
        prompt = 'Write the button label Show more'
        await self.html(turn_html(f'<div>{prompt}</div>'))
        await self.paint_result()
        self.assertEqual((await self.view(prompt))['matches'], 1)
        # the same words as a UI-less suffix must not be stripped from a different prompt
        self.assertEqual((await self.view('Write the button label'))['matches'], 0)

    async def test_prompt_that_itself_ends_with_an_ellipsis_matches_when_collapsed(self):
        prompt = 'Continue the story…'
        await self.html(turn_html(collapsed(prompt)))
        await self.paint_result()
        self.assertEqual((await self.view(prompt))['matches'], 1)

    async def test_prompt_ending_with_an_ellipsis_keeps_it_when_expanded(self):
        prompt = 'Continue the story…'
        await self.html(turn_html(expanded(prompt)))
        await self.paint_result()
        self.assertEqual((await self.view(prompt))['matches'], 1)
        # the same bubble must not match the prompt with its ellipsis removed
        self.assertEqual((await self.view('Continue the story'))['matches'], 0)

    async def test_ellipsis_without_a_separate_ui_span_is_prompt_text(self):
        prompt = 'Continue the story…'
        await self.html(turn_html('<div class="flex flex-col"><div>' + prompt + '</div><button>Show more</button></div>'))
        await self.paint_result()
        self.assertEqual((await self.view(prompt))['matches'], 1)

    async def test_prompt_ending_with_the_toggle_words_is_kept_beside_the_real_toggle(self):
        for prompt, bubble in (('Write the button label Show more', collapsed), ('Write the button label Show more', expanded),
                               ('Write the button label Show less', collapsed), ('Write the button label Show less', expanded)):
            with self.subTest(prompt=prompt, bubble=bubble.__name__):
                await self.html(turn_html(bubble(prompt)))
                await self.paint_result()
                self.assertEqual((await self.view(prompt))['matches'], 1)
                self.assertEqual((await self.view('Write the button label'))['matches'], 0)

    async def test_two_toggle_buttons_are_not_trusted(self):
        await self.html(turn_html('<div>Some prompt</div><button>Show more</button><button>Show less</button>'))
        await self.paint_result()
        self.assertEqual((await self.view('Some prompt'))['matches'], 0)

    async def test_observe_confirms_and_completes_a_collapsed_long_prompt_turn(self):
        await self.html(turn_html(collapsed(LONG_PROMPT)))
        await self.paint_result()
        receipt = headless.Receipt(self.root / 'run/receipt.json')
        receipt.create('long', LONG_PROMPT.strip())
        receipt.update(send_state='intent', send_clicks=1)
        self.page.url  # file:// page: no conversation URL is expected
        self.assertTrue(await headless.observe(self.page, receipt, wait_seconds=10))
        self.assertEqual((receipt.data['send_state'], receipt.data['generation_state']), ('confirmed', 'completed'))
        self.assertEqual(receipt.data['image_dimensions'], [[128, 128]])

    async def test_unverified_run_says_what_the_last_read_saw(self):
        await self.html(turn_html('<div>Some other bubble text</div>'))
        await self.paint_result()
        receipt = headless.Receipt(self.root / 'run/receipt.json')
        receipt.create('why', 'Create one vase')
        receipt.update(send_state='intent', send_clicks=1)
        self.assertFalse(await headless.observe(self.page, receipt, wait_seconds=0))
        self.assertEqual(receipt.data['reason'], 'posted_prompt_unverified')
        last = receipt.summary()['observation_last']
        self.assertEqual((last['matches'], last['user_count'], last['images']), (0, 1, 0))
        self.assertNotIn('Create one vase', json.dumps(last))


ATTACHMENTS = '''<form><div contenteditable="true" role="textbox" aria-label="Ask ChatGPT"></div>
<input type="file" multiple aria-label="Attach photos"><div data-composer-attachments></div>
<button type="button" aria-label="Send">Send</button></form>
<script>document.querySelector('input').onchange=e=>{const c=document.querySelector('[data-composer-attachments]');
 for(const f of e.target.files){
  const tile=document.createElement('div');tile.setAttribute('role','button');tile.setAttribute('aria-label',f.name);
  const img=document.createElement('img');img.alt=f.name;img.src='data:image/png;base64,IMAGE';tile.append(img);c.append(tile);
  const finish=()=>{const r=document.createElement('button');r.type='button';r.setAttribute('aria-label','Remove '+f.name);tile.append(r);};
  if(DELAY>=0) setTimeout(finish,DELAY);
 }};</script>'''


class UploadTransitionTests(BrowserCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.html('<main></main>')
        encoded = await self.page.evaluate("() => {const c=document.createElement('canvas');c.width=128;c.height=128;return c.toDataURL().split(',')[1]}")
        self.encoded = encoded
        self.source, self.guide = self.root / 'source.png', self.root / 'guide.png'
        self.source.write_bytes(base64.b64decode(encoded))
        self.guide.write_bytes(base64.b64decode(encoded))

    async def composer(self, delay):
        await self.html(ATTACHMENTS.replace('IMAGE', self.encoded).replace('DELAY', str(delay)))

    async def test_thumbnail_before_its_controls_is_waited_for_not_rejected(self):
        await self.composer(400)
        refs = image_files.snapshot_references(self.root / 'run', [self.source, self.guide])
        attachments = await image_files.upload_references(self.browser, self.page, refs)
        self.assertEqual([a['displayed_filename'] for a in attachments], ['source.png', 'guide.png'])

    async def test_markup_that_never_becomes_identifiable_is_named_after_the_grace_period(self):
        await self.composer(-1)          # thumbnails only, no removal control ever
        self.browser.unrecognized_grace_seconds = 0.6
        refs = image_files.snapshot_references(self.root / 'run', [self.source, self.guide])
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.upload_references(self.browser, self.page, refs)
        self.assertEqual(error.exception.code, 'attachment_markup_unrecognized')
        self.assertEqual(len(error.exception.diagnostic['upload']['tiles']), 2)


HOVER_REVEAL = '''<style>.tile{position:relative;width:96px;height:96px}
.tile img{position:absolute;inset:0;width:100%;height:100%;z-index:2}
.tile button{position:absolute;top:0;right:0;z-index:1;pointer-events:none;opacity:0}
.tile:hover button{pointer-events:auto;opacity:1;z-index:3}</style>
<form><div contenteditable="true" role="textbox" aria-label="Ask ChatGPT"></div>
<input type="file" multiple aria-label="Attach photos"><div data-composer-attachments></div>
<button type="button" aria-label="Send">Send</button></form>
<script>document.querySelector('input').onchange=e=>{const c=document.querySelector('[data-composer-attachments]');
 for(const f of e.target.files){
  const tile=document.createElement('div');tile.className='tile';tile.setAttribute('role','button');tile.setAttribute('aria-label',f.name);
  const r=document.createElement('button');r.type='button';r.setAttribute('aria-label','Remove '+f.name);r.onclick=()=>tile.remove();tile.append(r);
  const img=document.createElement('img');img.alt=f.name;img.src='data:image/png;base64,IMAGE';tile.append(img);c.append(tile);
 }};</script>'''


class HoverRevealCleanupTests(UploadTransitionTests):
    """Live UI: the removal control ignores the pointer until its tile is hovered, and the thumbnail covers it."""

    async def test_probe_cleanup_hovers_the_tile_before_clicking_remove(self):
        await self.html(HOVER_REVEAL.replace('IMAGE', self.encoded))
        refs = image_files.snapshot_references(self.root / 'run', [self.source, self.guide])
        attachments = await image_files.upload_references(self.browser, self.page, refs)
        self.browser.context.set_default_timeout(1500)
        await image_files.remove_probe_attachments(self.browser, self.page, attachments)
        self.assertEqual(await self.browser.attachment_tiles(self.page), [])

    async def test_a_control_that_cannot_be_clicked_fails_with_a_named_step(self):
        await self.html(HOVER_REVEAL.replace('IMAGE', self.encoded).replace('.tile:hover button{pointer-events:auto', '.never button{pointer-events:auto'))
        refs = image_files.snapshot_references(self.root / 'run', [self.source])
        attachments = await image_files.upload_references(self.browser, self.page, refs)
        self.browser.context.set_default_timeout(800)
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.remove_probe_attachments(self.browser, self.page, attachments)
        self.assertEqual(error.exception.code, 'probe_cleanup_failed')
        self.assertEqual(error.exception.diagnostic['cleanup'], {'attachment': 'source.png', 'error': 'TimeoutError'})

    async def test_decode_timeout_is_a_named_error(self):
        async def slow(*_, **__):
            await __import__('asyncio').sleep(1)
        original = image_files.asyncio.timeout
        with patch.object(self.page, 'evaluate', slow), patch.object(image_files.asyncio, 'timeout', lambda _: original(0.05)):
            with self.assertRaises(headless.ResearchError) as error:
                await image_files.decode_image(self.page, PIXEL, 'image/png')
        self.assertEqual(error.exception.code, 'image_decode_timeout')


class DisplayedNameTests(unittest.TestCase):
    def test_dedupe_renames_seen_live_are_accepted_and_lookalikes_are_not(self):
        path = Path('/x/1_source.png')
        names = ['1_source(10).png', '1_source(20260930-153646).png', '1_source.png']
        for name in names:
            self.assertEqual(image_files.displayed_candidates(path, [name]), [name])
        for name in ('1_source(0).png', '1_source(2026-09-30).png', '1_source(20260930-15).png', '1_source (2).png',
                     '11_source(2).png', '1_source(2).jpg', '1_source(2)x.png', None):
            self.assertEqual(image_files.displayed_candidates(path, [name]), [], name)


class ImageStructureTests(BrowserCase):
    """validate_image_structure: catches truncated/garbage references before any receipt exists (the browser decode stays authoritative)."""

    async def samples(self):
        await self.html('<main></main>')
        out = {}
        for mime in ('image/png', 'image/jpeg', 'image/webp'):
            out[mime] = base64.b64decode(await self.page.evaluate(
                "m => {const c=document.createElement('canvas');c.width=64;c.height=48;const x=c.getContext('2d');"
                "x.fillStyle='teal';x.fillRect(0,0,64,48);x.fillStyle='white';x.fillRect(8,8,20,20);return c.toDataURL(m).split(',')[1]}", mime))
        return out

    async def test_real_browser_encoded_images_pass_and_damaged_copies_fail(self):
        samples = await self.samples()
        for mime, raw in samples.items():
            with self.subTest(mime=mime, case='valid'):
                image_files.validate_image_structure(raw, mime)
            damaged = {'truncated': raw[:len(raw) // 2], 'garbage': b'\x00' * 200, 'tiny': raw[:10], 'text': b'not an image at all'}
            if mime == 'image/png':
                flipped = bytearray(raw)
                flipped[17] ^= 0xFF                              # width field: IHDR CRC no longer matches
                damaged['ihdr_crc'] = bytes(flipped)
            for case, data in damaged.items():
                with self.subTest(mime=mime, case=case), self.assertRaises(headless.ResearchError) as error:
                    image_files.validate_image_structure(data, mime)
                self.assertEqual(error.exception.code, 'reference_image_invalid')
        png = samples['image/png']
        with self.assertRaises(headless.ResearchError):
            image_files.validate_image_structure(png, 'image/jpeg')      # right bytes, wrong declared format
        image_files.validate_image_structure(png + b'trailing bytes', 'image/png')   # trailing data after IEND is legal

    @staticmethod
    def png_chunks(raw):
        position, found = 8, []
        while position < len(raw):
            length = struct.unpack('>I', raw[position:position + 4])[0]
            found.append((raw[position + 4:position + 8], position, position + 12 + length))
            position += 12 + length
        return found

    @staticmethod
    def chunk(kind, data, crc=None):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) if crc is None else crc)

    async def test_png_chunk_damage_is_caught_before_any_receipt(self):
        png = (await self.samples())['image/png']
        chunks = {}
        for kind, start, end in self.png_chunks(png):
            chunks.setdefault(kind, (start, end))            # the FIRST chunk of each kind (Chrome may split IDAT in two)
        idat, iend = chunks[b'IDAT'], chunks[b'IEND']
        def flip(index):
            damaged = bytearray(png)
            damaged[index] ^= 0xFF
            return bytes(damaged)
        damaged = {
            'idat_data_byte': flip(idat[0] + 8),
            'idat_crc': flip(idat[1] - 1),
            'iend_crc': flip(iend[1] - 1),
            'no_idat': png[:8] + b''.join(png[a:b] for kind, a, b in self.png_chunks(png) if kind != b'IDAT'),
            'iend_missing': png[:iend[0]],
            'cut_inside_idat': png[:idat[0] + 20],
            'idat_length_overshoots': png[:idat[0]] + struct.pack('>I', 0x7FFFFFFF) + png[idat[0] + 4:],
            'idat_length_not_a_length': png[:idat[0]] + b'\xff\xff\xff\xff' + png[idat[0] + 4:],
            'iend_with_data': png[:iend[0]] + self.chunk(b'IEND', b'x') ,
            'ihdr_not_first': png[:8] + png[chunks[b'IDAT'][0]:chunks[b'IDAT'][1]] + png[8:chunks[b'IDAT'][0]] + png[chunks[b'IDAT'][1]:],
        }
        for case, data in damaged.items():
            with self.subTest(case=case), self.assertRaises(headless.ResearchError) as error:
                image_files.validate_image_structure(data, 'image/png')
            self.assertEqual(error.exception.code, 'reference_image_invalid')
        # A damaged ancillary chunk is rejected too: Chromium (below) skips it and decodes the image, Pillow rejects the whole file,
        # so the same bytes would mean different things downstream. Well-formed ancillary chunks and trailing bytes still pass.
        ancillary = png[:idat[0]] + self.chunk(b'tEXt', b'Comment\x00hello', crc=0) + png[idat[0]:]
        with self.assertRaises(headless.ResearchError) as error:
            image_files.validate_image_structure(ancillary, 'image/png')
        self.assertEqual(error.exception.code, 'reference_image_invalid')
        await image_files.decode_image(self.page, ancillary, 'image/png')        # why the preflight is needed: Chromium decodes it
        fine = png[:idat[0]] + self.chunk(b'tEXt', b'Comment\x00hello') + png[idat[0]:]
        for case, data in {'trailing': png + b'\x00' * 7, 'well_formed_ancillary': fine}.items():
            with self.subTest(case=case):
                image_files.validate_image_structure(data, 'image/png')

    async def test_duplicate_ihdr_is_rejected_before_any_receipt(self):
        png = (await self.samples())['image/png']
        first_end = 8 + 12 + struct.unpack('>I', png[8:12])[0]
        duplicate = png[:first_end] + png[8:first_end] + png[first_end:]
        with self.assertRaises(headless.ResearchError) as error:
            image_files.validate_image_structure(duplicate, 'image/png')
        self.assertEqual(error.exception.code, 'reference_image_invalid')

    async def test_jpeg_segment_damage_is_caught_and_real_layouts_pass(self):
        jpeg = (await self.samples())['image/jpeg']
        sof = jpeg.index(b'\xff\xc0')
        self.assertEqual(jpeg[sof + 2:sof + 4], b'\x00\x11')                        # baseline, 3 components: length 17
        def patched(index, value):
            return jpeg[:index] + value + jpeg[index + len(value):]
        damaged = {
            'sof_length_2': patched(sof + 2, b'\x00\x02'),
            'sof_length_below_minimum': patched(sof + 2, b'\x00\x0a'),
            'sof_components_disagree_with_length': patched(sof + 9, b'\x04'),
            'sof_zero_components': patched(sof + 9, b'\x00'),
            'sof_zero_width': patched(sof + 7, b'\x00\x00'),
            'sof_length_overshoots_file': patched(sof + 2, b'\xff\xf0'),
            'segment_length_zero': patched(2 + 2, b'\x00\x00') if jpeg[2] == 0xFF and jpeg[3] != 0xC0 else patched(sof + 2, b'\x00\x00'),
            'cut_right_after_the_sof_marker': jpeg[:sof + 2],
            'cut_inside_the_sof': jpeg[:sof + 6] + b'\xff\xd9',
            'no_frame_header': jpeg[:2] + b'\xff\xfe\x00\x04hi' + b'\xff\xd9',
        }
        for case, data in damaged.items():
            with self.subTest(case=case), self.assertRaises(headless.ResearchError) as error:
                image_files.validate_image_structure(data, 'image/jpeg')
            self.assertEqual(error.exception.code, 'reference_image_invalid', case)
        exif = b'\xff\xe1' + struct.pack('>H', 2 + 8) + b'Exif\x00\x00II'
        progressive = patched(sof + 1, b'\xc2')
        for case, data in {'exif_app1_before_the_frame': jpeg[:2] + exif + jpeg[2:], 'progressive_frame': progressive,
                           'fill_bytes_before_a_marker': jpeg[:2] + b'\xff\xff' + jpeg[2:]}.items():
            with self.subTest(case=case):
                image_files.validate_image_structure(data, 'image/jpeg')

    async def test_no_truncation_of_any_format_raises_anything_but_the_named_error(self):
        for mime, raw in (await self.samples()).items():
            step = max(1, len(raw) // 120)
            for cut in list(range(0, len(raw), step)) + [len(raw) - 1, len(raw) - 2]:
                with self.subTest(mime=mime, cut=cut):
                    try:
                        image_files.validate_image_structure(raw[:cut], mime)
                    except headless.ResearchError as error:
                        self.assertEqual(error.code, 'reference_image_invalid')
                    else:
                        if mime != 'image/jpeg':                                     # a JPEG may lose only its last bytes after EOI
                            self.assertEqual(cut, len(raw), 'a cut image must not pass')

    async def test_a_corrupt_reference_leaves_no_receipt_in_any_entry_path(self):
        source = self.root / 'source.png'
        source.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 40)            # right signature, nothing behind it
        folder = self.root / 'case'
        folder.mkdir()
        (folder / 'prompt.txt').write_text('Edit the canvas.')
        (folder / '1_source.png').write_bytes(source.read_bytes())
        (folder / 'package.json').write_text(json.dumps({'order': ['1_source.png']}))
        with self.assertRaises(headless.ResearchError) as error:
            package_run.create_runs(self.root / 'state', [folder], self.root / 'out')
        self.assertEqual(error.exception.code, 'reference_image_invalid')
        self.assertFalse((self.root / 'state/headless-images').exists())
        with self.assertRaises(headless.ResearchError):
            image_files.validate_reference_paths([source])
        png = bytearray(PIXEL)                                               # a real PNG whose IDAT checksum no longer matches
        idat = bytes(png).index(b'IDAT')
        png[idat + 4] ^= 0xFF
        (folder / '1_source.png').write_bytes(bytes(png))
        with self.assertRaises(headless.ResearchError) as again:
            package_run.create_runs(self.root / 'state', [folder], self.root / 'out')
        self.assertEqual(again.exception.code, 'reference_image_invalid')
        self.assertFalse((self.root / 'state/headless-images').exists())
        self.assertEqual(headless.classify_error('reference_image_invalid'), 'input')


class BusyUploadGraceTests(UploadTransitionTests):
    """Codex review: the unrecognised-tile grace must not run out while an upload is still busy."""

    async def test_controls_that_appear_after_a_long_busy_upload_are_waited_for(self):
        await self.html(ATTACHMENTS.replace('IMAGE', self.encoded).replace('DELAY', '2500').replace(
            'const tile=document.createElement(\'div\');',
            "const tile=document.createElement('div');const bar=document.createElement('span');bar.setAttribute('role','progressbar');"
            "bar.style.cssText='display:inline-block;width:40px;height:8px;background:#999';tile.append(bar);setTimeout(()=>bar.remove(),2600);"))
        self.browser.unrecognized_grace_seconds = 1.0            # far shorter than the 2.6 s busy upload
        refs = image_files.snapshot_references(self.root / 'run', [self.source, self.guide])
        attachments = await image_files.upload_references(self.browser, self.page, refs)
        self.assertEqual([a['displayed_filename'] for a in attachments], ['source.png', 'guide.png'])

    async def test_markup_that_stays_unidentifiable_once_nothing_is_busy_is_still_named(self):
        await self.html(ATTACHMENTS.replace('IMAGE', self.encoded).replace('DELAY', '-1'))
        self.browser.unrecognized_grace_seconds = 0.6
        refs = image_files.snapshot_references(self.root / 'run', [self.source, self.guide])
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.upload_references(self.browser, self.page, refs)
        self.assertEqual(error.exception.code, 'attachment_markup_unrecognized')


class RunCreationTests(unittest.TestCase):
    def package(self, root, name, *, empty_guide=False):
        folder = root / name
        folder.mkdir()
        (folder / 'prompt.txt').write_text('Edit the canvas.')
        (folder / '1_source.png').write_bytes(PIXEL)
        (folder / '2_guide.png').write_bytes(b'' if empty_guide else PIXEL + b'guide')
        (folder / 'package.json').write_text(json.dumps({'order': ['1_source.png', '2_guide.png']}))
        return folder

    def test_a_bad_second_package_leaves_no_receipt_for_the_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good, bad = self.package(root, 'good'), self.package(root, 'bad', empty_guide=True)
            with self.assertRaises(headless.ResearchError) as error:
                package_run.create_runs(root, [good, bad], root / 'out')
            self.assertEqual(error.exception.code, 'image_size_invalid')
            self.assertEqual(list((root / 'headless-images').glob('*/receipt.json')) if (root / 'headless-images').exists() else [], [])


class NativeFormatTests(BrowserCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.browser.close()
        await self.browser.start(headless=True, accept_downloads=True)
        self.page = await self.browser.context.new_page()
        await self.html('<main></main>')
        self.webp = base64.b64decode(await self.page.evaluate(
            "() => {const c=document.createElement('canvas');c.width=128;c.height=128;const x=c.getContext('2d');x.fillStyle='blue';x.fillRect(0,0,128,128);return c.toDataURL('image/webp').split(',')[1]}"))

    async def receipt(self, destination, key='run'):
        encoded = base64.b64encode(self.webp).decode()
        await self.html(f'''<main><button aria-label="Owned.png">Owned.png</button><img alt="Owned.png" width="300" src="data:image/webp;base64,{encoded}">
<button aria-label="Download file" onclick="const a=document.createElement('a');a.href='data:image/webp;base64,{encoded}';a.download='Owned.webp';a.click();">Download</button></main>''')
        await self.page.wait_for_function('() => document.images[0].complete&&document.images[0].naturalWidth===128')
        fingerprint = await image_files.decode_image(self.page, self.webp, 'image/webp')
        receipt = headless.Receipt(self.root / f'{key}/receipt.json')
        receipt.create(f'native-{key}', 'Create a blue square')
        receipt.update(send_state='confirmed', send_clicks=1, generation_state='completed', library_verified=True,
                       library_card_names=['Owned.png'], image_dimensions=[[128, 128]], image_fingerprints=[fingerprint],
                       download_file=str(destination))
        return receipt

    async def test_explicit_destination_naming_another_format_is_refused_without_writing(self):
        destination = self.root / 'wanted.png'
        receipt = await self.receipt(destination)
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.download_owned(self.page, receipt, headless.VIEWER_FINGERPRINTS)
        self.assertEqual(error.exception.code, 'download_extension_mismatch')
        self.assertEqual(error.exception.diagnostic['download'], {'native_suffix': '.webp', 'requested_suffix': '.png'})
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob('.chatgpt-original-*')), [])
        self.assertFalse(receipt.data['downloaded'])

    async def test_explicit_destination_with_an_unknown_or_missing_extension_is_refused_too(self):
        for name in ('wanted.tiff', 'wanted.txt', 'wanted'):
            with self.subTest(name=name):
                destination = self.root / name
                receipt = await self.receipt(destination, key='case_' + name.replace('.', '_'))
                with self.assertRaises(headless.ResearchError) as error:
                    await image_files.download_owned(self.page, receipt, headless.VIEWER_FINGERPRINTS)
                self.assertEqual(error.exception.code, 'download_extension_mismatch')
                self.assertEqual(error.exception.diagnostic['download']['native_suffix'], '.webp')
                self.assertFalse(destination.exists())
                self.assertEqual(list(self.root.glob('.chatgpt-original-*')), [])
                self.assertFalse(receipt.data['downloaded'])

    async def test_explicit_destination_with_the_native_extension_is_published(self):
        destination = self.root / 'wanted.webp'
        receipt = await self.receipt(destination)
        await image_files.download_owned(self.page, receipt, headless.VIEWER_FINGERPRINTS)
        self.assertEqual(destination.read_bytes(), self.webp)
        self.assertTrue(receipt.data['downloaded'])


class MeasurementTests(unittest.IsolatedAsyncioTestCase):
    """The numbers latency work depends on: probe timings, code fingerprint, browser-session cost."""

    def probe_args(self, root, references=()):
        return argparse.Namespace(command='probe', state_dir=root, pause_idle_worker=False, reference=list(references))

    async def test_probe_reports_ordered_timings_without_any_send(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.png'
            source.write_bytes(PIXEL)

            @contextlib.asynccontextmanager
            async def local_session(*_, **__):
                yield MagicMock()

            uploaded = [{'displayed_filename': 'source.png'}]
            with patch.object(headless, 'session', local_session), \
                 patch.object(headless, 'prepare', AsyncMock(return_value=(MagicMock(), None, None, []))), \
                 patch.object(headless, 'upload_references', AsyncMock(return_value=uploaded)), \
                 patch.object(headless, 'remove_probe_attachments', AsyncMock()):
                result = await headless.execute(self.probe_args(root, [source]), None)
        timings = result['timings']
        self.assertEqual(list(timings), ['browser_started', 'composer_ready', 'upload_ready', 'cleanup_done'])
        self.assertEqual(sorted(timings.values()), list(timings.values()))
        self.assertEqual((result['send_state'], result['skill_sha256']), ('not_sent', headless.skill_fingerprint()))

    def test_receipt_is_bound_to_the_code_that_created_it(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = headless.Receipt(Path(directory) / 'r/receipt.json')
            receipt.create('bound', 'Create one vase')
            self.assertRegex(receipt.summary()['skill_sha256'], r'^[0-9a-f]{64}$')
            self.assertEqual(receipt.data['skill_sha256'], headless.skill_fingerprint())

    def test_fingerprint_changes_when_a_script_changes(self):
        before = headless.skill_fingerprint()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            copy = root / 'scripts/headless.py'
            copy.write_bytes(SCRIPT.read_bytes())
            (root / 'scripts/image_files.py').write_text('x = 1\n')
            spec = importlib.util.spec_from_file_location('fingerprint_copy', copy)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            first = module.skill_fingerprint()
            (root / 'scripts/image_files.py').write_text('x = 2\n')
            self.assertNotEqual(first, module.skill_fingerprint())
        self.assertEqual(before, headless.skill_fingerprint())

    async def test_runner_records_the_browser_session_cost_in_every_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipts = []
            for name in ('a', 'b'):
                receipt = headless.Receipt(root / f'headless-images/{name}/receipt.json')
                receipt.create(name, 'Create one vase')
                receipts.append(receipt)

            @contextlib.asynccontextmanager
            async def local_session(*_, **__):
                yield MagicMock()

            args = argparse.Namespace(state_dir=root, pause_idle_worker=False, concurrency=2, wait_seconds=1, verify_library=False)
            # package_run imports the module by name, so patch that instance
            with patch.object(package_run.headless, 'session', local_session), \
                 patch.object(package_run, 'run_one', AsyncMock(return_value={})):
                result = await package_run.run_all(args, receipts)
        self.assertGreaterEqual(result['session_seconds'], 0)
        self.assertLessEqual(result['session_seconds'], result['wall_seconds'])
        self.assertEqual([r.data['session_seconds'] for r in receipts], [result['session_seconds']] * 2)


if __name__ == '__main__':
    unittest.main()
