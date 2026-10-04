import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/headless.py'
SPEC = importlib.util.spec_from_file_location('headless_image_files_tests', SCRIPT)
headless = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(headless)
import image_files

PIXEL = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')


class FormatStructureTests(unittest.TestCase):
    @staticmethod
    def chunk(kind, data, crc=None):
        value = zlib.crc32(kind + data) & 0xFFFFFFFF if crc is None else crc
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', value)

    @staticmethod
    def png_chunks(raw):
        chunks, position = [], 8
        while position < len(raw):
            length = struct.unpack('>I', raw[position:position + 4])[0]
            kind = raw[position + 4:position + 8]
            data = raw[position + 8:position + 8 + length]
            chunks.append((kind, data))
            position += length + 12
        return chunks

    def test_png_chunk_count_order_and_every_crc_are_checked(self):
        chunks = self.png_chunks(PIXEL)
        ihdr = next(data for kind, data in chunks if kind == b'IHDR')
        idat = next(data for kind, data in chunks if kind == b'IDAT')
        half = max(1, len(idat) // 2)
        signature = PIXEL[:8]
        end = self.chunk(b'IEND', b'')

        malformed = {
            'duplicate_ihdr': signature + self.chunk(b'IHDR', ihdr) + self.chunk(b'IHDR', ihdr)
                               + self.chunk(b'IDAT', idat) + end,
            'idat_not_consecutive': signature + self.chunk(b'IHDR', ihdr)
                                    + self.chunk(b'IDAT', idat[:half])
                                    + self.chunk(b'tEXt', b'Comment\x00x')
                                    + self.chunk(b'IDAT', idat[half:]) + end,
            'unknown_critical': signature + self.chunk(b'IHDR', ihdr)
                               + self.chunk(b'ABCD', b'x') + self.chunk(b'IDAT', idat) + end,
            # Chromium skips a damaged tEXt chunk but Pillow rejects the file: damage means different things to different decoders.
            'ancillary_bad_crc': signature + self.chunk(b'IHDR', ihdr) + self.chunk(b'tEXt', b'Comment\x00x', crc=0)
                                 + self.chunk(b'IDAT', idat) + end,
        }
        for case, raw in malformed.items():
            with self.subTest(case=case):
                with self.assertRaises(headless.ResearchError) as error:
                    image_files.validate_image_structure(raw, 'image/png')
                self.assertEqual(error.exception.code, 'reference_image_invalid')

        valid_split = signature + self.chunk(b'IHDR', ihdr) + self.chunk(b'IDAT', idat[:half])
        valid_split += self.chunk(b'IDAT', idat[half:]) + end
        image_files.validate_image_structure(valid_split, 'image/png')
        with_ancillary = signature + self.chunk(b'IHDR', ihdr) + self.chunk(b'tEXt', b'Comment\x00x') + self.chunk(b'IDAT', idat) + end
        image_files.validate_image_structure(with_ancillary, 'image/png')            # a well-formed ancillary chunk is fine

    @staticmethod
    def jpeg_frame(*, marker=0xC0, components=1, height=1, width=1):
        details = b''.join(bytes((index, 0x11, 0)) for index in range(1, components + 1))
        payload = b'\x08' + struct.pack('>HHB', height, width, components) + details
        return b'\xff\xd8\xff' + bytes((marker,)) + struct.pack('>H', len(payload) + 2) + payload

    @staticmethod
    def jpeg_scan(component_ids):
        count = len(component_ids)
        payload = bytes((count,)) + b''.join(bytes((component, 0)) for component in component_ids)
        payload += b'\x00\x3f\x00'
        return b'\xff\xda' + struct.pack('>H', len(payload) + 2) + payload

    def test_jpeg_sof_must_be_followed_by_a_nonempty_scan(self):
        no_scan = self.jpeg_frame() + b'\xff\xd9'
        empty_scan = self.jpeg_frame() + self.jpeg_scan([1]) + b'\xff\xd9'
        for case, raw in {'no_sos': no_scan, 'empty_scan': empty_scan}.items():
            with self.subTest(case=case):
                with self.assertRaises(headless.ResearchError) as error:
                    image_files.validate_image_structure(raw, 'image/jpeg')
                self.assertEqual(error.exception.code, 'reference_image_invalid')

    def test_a_scan_may_not_name_a_component_the_frame_lacks(self):
        raw = self.jpeg_frame() + self.jpeg_scan([2]) + b'\x00\xff\xd9'
        with self.assertRaises(headless.ResearchError) as error:
            image_files.validate_image_structure(raw, 'image/jpeg')
        self.assertEqual(error.exception.code, 'reference_image_invalid')

    def test_with_unique_frame_ids_a_repeated_or_out_of_order_selector_is_rejected(self):
        # Measured 2026-10-01: Chromium and Pillow both reject these (see decoder_differential.json).
        well_formed = self.jpeg_frame(components=3) + self.jpeg_scan([1, 2, 3]) + b'\x00\xff\xd9'
        image_files.validate_image_structure(well_formed, 'image/jpeg')            # same frame: only the selectors differ below
        for case, ids in {'duplicate_selector': [1, 1, 3], 'out_of_order': [2, 1, 3]}.items():
            with self.subTest(case=case), self.assertRaises(headless.ResearchError) as error:
                image_files.validate_image_structure(self.jpeg_frame(components=3) + self.jpeg_scan(ids) + b'\x00\xff\xd9', 'image/jpeg')
            self.assertEqual(error.exception.code, 'reference_image_invalid')

    def test_component_id_quirks_that_real_decoders_accept_are_not_rejected(self):
        # Measured 2026-10-01: Chromium renders identical pixels and Pillow decodes a JPEG whose components all have id 0
        # (libjpeg-style writers). When the frame's own ids repeat, selector uniqueness/order cannot be judged: left to the decoders.
        zero_frame = bytearray(self.jpeg_frame(components=3))
        sof = zero_frame.index(b'\xff\xc0')
        for offset in range(3):
            zero_frame[sof + 10 + 3 * offset] = 0
        image_files.validate_image_structure(bytes(zero_frame) + self.jpeg_scan([0, 0, 0]) + b'\x00\xff\xd9', 'image/jpeg')

    def test_only_one_three_or_four_frame_components_are_accepted(self):
        # T.81 would allow 2..255 for sequential frames (progressive 1..4), but Chromium and Pillow both reject every other count on
        # genuine files (measured with a minimal encoder, see decoder_differential.json); nothing real uses them.
        for components in (1, 3, 4):
            ids = list(range(1, components + 1))
            with self.subTest(components=components):
                image_files.validate_image_structure(self.jpeg_frame(components=components) + self.jpeg_scan(ids) + b'\x00\xff\xd9', 'image/jpeg')
        for components in (2, 5, 6, 10, 11):
            ids = list(range(1, components + 1))
            with self.subTest(components=components), self.assertRaises(headless.ResearchError) as error:
                image_files.validate_image_structure(
                    self.jpeg_frame(components=components) + self.jpeg_scan(ids[:4]) + b'\x00\xff\xd9', 'image/jpeg')
            self.assertEqual(error.exception.code, 'reference_image_invalid')

    def test_hierarchical_and_differential_frames_are_rejected(self):
        for marker in (0xC5, 0xC6, 0xC7, 0xCD, 0xCE, 0xCF):
            with self.subTest(marker=hex(marker)), self.assertRaises(headless.ResearchError):
                image_files.validate_image_structure(self.jpeg_frame(marker=marker) + self.jpeg_scan([1]) + b'\x00\xff\xd9', 'image/jpeg')

    def test_a_large_scan_is_checked_in_time_proportional_to_its_markers_not_its_bytes(self):
        # Entropy data is most of a real file. A per-byte Python loop took ~65 ms per MB (a 20 MiB reference: over a second
        # before any browser starts); jumping between 0xFF bytes takes a few ms. The bound is 15x the measured time.
        import time
        raw = self.jpeg_frame() + self.jpeg_scan([1]) + b'\x12' * (16 * 1024 * 1024) + b'\xff\x00' + b'\x34' * 1024 + b'\xff\xd9'
        started = time.perf_counter()
        image_files.validate_image_structure(raw, 'image/jpeg')
        self.assertLess(time.perf_counter() - started, 0.3)

    def test_zero_height_frames_are_rejected_and_dnl_segments_are_just_skipped(self):
        # A zero SOF height means "given by a DNL segment"; Chromium and Pillow both reject such a frame even with a valid DNL.
        scan = self.jpeg_scan([1])
        zero = self.jpeg_frame(height=0) + scan + b'\x00\xff\xdc\x00\x04\x00\x01\xff\xd9'
        with self.assertRaises(headless.ResearchError) as error:
            image_files.validate_image_structure(zero, 'image/jpeg')
        self.assertEqual(error.exception.code, 'reference_image_invalid')
        # A DNL (even two) in a normal frame is harmless to both decoders, so it is not damage.
        normal = self.jpeg_frame(height=1) + scan + b'\x00\xff\xdc\x00\x04\x00\x01\xff\xdc\x00\x04\x00\x01\xff\xd9'
        image_files.validate_image_structure(normal, 'image/jpeg')

    def test_jpeg_sof_precision_must_fit_the_process(self):
        for marker, precision, ok in ((0xC0, 8, True), (0xC0, 12, False), (0xC0, 4, False), (0xC0, 0, False), (0xC1, 12, True),
                                      (0xC2, 12, True), (0xC1, 16, False), (0xC3, 16, True), (0xC3, 1, False)):
            raw = bytearray(self.jpeg_frame(marker=marker))
            raw[raw.index(bytes((0xFF, marker))) + 4] = precision
            raw = bytes(raw) + self.jpeg_scan([1]) + b'\x00\xff\xd9'
            with self.subTest(marker=hex(marker), precision=precision):
                if ok:
                    image_files.validate_image_structure(raw, 'image/jpeg')
                else:
                    with self.assertRaises(headless.ResearchError):
                        image_files.validate_image_structure(raw, 'image/jpeg')

    def test_png_header_fields_and_palette_size_follow_the_decoders(self):
        chunks = self.png_chunks(PIXEL)
        ihdr = bytearray(next(data for kind, data in chunks if kind == b'IHDR'))
        idat = next(data for kind, data in chunks if kind == b'IDAT')
        end = self.chunk(b'IEND', b'')
        def png(**fields):
            header = bytearray(ihdr)
            for index, value in fields.items():
                header[int(index[1:])] = value
            return PIXEL[:8] + self.chunk(b'IHDR', bytes(header)) + self.chunk(b'IDAT', idat) + end
        self.assertEqual((ihdr[8], ihdr[9]), (8, 4))                               # the shared 1x1 fixture is 8-bit gray+alpha
        image_files.validate_image_structure(png(), 'image/png')
        for case, fields in {'depth_3': {'b8': 3}, 'rgb_depth_4': {'b8': 4, 'b9': 2}, 'color_type_5': {'b9': 5},
                             'compression_1': {'b10': 1}, 'filter_method_1': {'b11': 1}, 'interlace_2': {'b12': 2}}.items():
            with self.subTest(case=case), self.assertRaises(headless.ResearchError):
                image_files.validate_image_structure(png(**fields), 'image/png')
        palette = bytearray(ihdr); palette[8:10] = bytes((4, 3))                   # 4-bit palette: at most 16 entries
        def with_palette(entries):
            return PIXEL[:8] + self.chunk(b'IHDR', bytes(palette)) + self.chunk(b'PLTE', bytes(3 * entries)) + self.chunk(b'IDAT', idat) + end
        image_files.validate_image_structure(with_palette(16), 'image/png')
        with self.assertRaises(headless.ResearchError):
            image_files.validate_image_structure(with_palette(17), 'image/png')


class SnapshotTests(unittest.TestCase):
    def test_reference_order_bytes_hashes_and_source_changes_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, guide = root/'source.png', root/'guide.png'
            source.write_bytes(PIXEL)
            guide.write_bytes(PIXEL + b'original-guide')
            refs = image_files.snapshot_references(root/'run', [source,guide])
            source.write_bytes(b'changed input')
            self.assertEqual([r['filename'] for r in refs],['source.png','guide.png'])
            self.assertEqual([r['order'] for r in refs],[1,2])
            self.assertEqual(image_files.reference_bytes(refs[0]),PIXEL)
            self.assertEqual(refs[0]['sha256'],hashlib.sha256(PIXEL).hexdigest())
            self.assertEqual(Path(refs[0]['snapshot_path']).stat().st_mode & 0o777,0o600)

    def test_changed_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/'source.png'
            source.write_bytes(PIXEL)
            ref=image_files.snapshot_references(root/'run',[source])[0]
            Path(ref['snapshot_path']).write_bytes(b'changed')
            with self.assertRaises(headless.ResearchError) as error:
                image_files.reference_bytes(ref)
            self.assertEqual(error.exception.code,'reference_snapshot_changed')

    def test_duplicate_names_are_rejected_before_snapshotting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=root/'source.png'
            second=root/'another/source.png'
            second.parent.mkdir()
            first.write_bytes(PIXEL)
            second.write_bytes(PIXEL)
            with self.assertRaises(headless.ResearchError):
                image_files.snapshot_references(root/'run',[first,second])
            self.assertFalse((root/'run').exists())

    def test_existing_download_destination_blocks_before_browser_or_send(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            target=root/'native.png'
            target.write_bytes(b'preserve me')
            prompt=root/'prompt.txt'
            prompt.write_text('Create one image')
            args=MagicMock(command='generate',state_dir=root,run_id='new',prompt_file=prompt,download_file=target)
            with patch.object(headless,'execute') as execute,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(headless.run_command(args),2)
                execute.assert_not_called()
            self.assertEqual(target.read_bytes(),b'preserve me')


class ReferenceSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_reference_preparation_blocks_before_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt=headless.Receipt(Path(directory)/'receipt.json')
            receipt.create('incomplete','Use both reference images',reference_count=2)
            args=MagicMock(command='resume')
            with patch.object(headless,'session') as session:
                with self.assertRaises(headless.ResearchError) as error:
                    await headless.execute(args,receipt)
                session.assert_not_called()
            self.assertEqual(error.exception.code,'reference_preparation_incomplete')
            self.assertEqual(receipt.data['send_clicks'],0)

    async def test_attachment_change_after_fill_prevents_send(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt=headless.Receipt(Path(directory)/'receipt.json')
            receipt.create('reference-run','Edit image 2 using image 1')
            receipt.update(attachments=[{'displayed_filename':'source.png'}])
            editor=MagicMock(fill=AsyncMock(),inner_text=AsyncMock(return_value=receipt.data['prompt']))
            scope=MagicMock()
            send=MagicMock(click=AsyncMock())
            browser=MagicMock()
            with patch.object(headless,'validate_attachment_order',AsyncMock(side_effect=headless.ResearchError('upload_lost','Input disappeared'))),patch.object(headless,'required',AsyncMock(return_value=send)):
                with self.assertRaises(headless.ResearchError):
                    await headless.submit_once(MagicMock(),editor,scope,receipt,browser=browser)
            self.assertEqual(receipt.data['send_state'],'not_sent')
            send.click.assert_not_awaited()


class LocalBrowserFilesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.browser=headless.Browser(self.root,headless.load_config(headless.DEFAULT_STATE))
        await self.browser.start(headless=True,accept_downloads=True)
        self.addAsyncCleanup(self.browser.close)
        self.page=await self.browser.context.new_page()
        await self.html('<main></main>')
        encoded=await self.page.evaluate("() => {const c=document.createElement('canvas');c.width=128;c.height=128;const x=c.getContext('2d');x.fillStyle='blue';x.fillRect(0,0,128,128);return c.toDataURL().split(',')[1]}")
        self.raw=base64.b64decode(encoded)
        self.source=self.root/'source.png'
        self.guide=self.root/'guide.png'
        self.source.write_bytes(self.raw)
        self.guide.write_bytes(self.raw)

    async def html(self, content):
        fixture=self.root/'fixture.html'
        fixture.write_text(content)
        await self.page.goto(fixture.as_uri(),wait_until='domcontentloaded')

    async def attachment_fixture(self, *, reverse=False, waiting=False):
        await self.html('''<button aria-pressed="true">Chat</button><form>
<button aria-label="Remove Create image">Create image</button>
<div contenteditable="true" role="textbox" aria-label="Ask ChatGPT"></div>
<input type="file" multiple><div id="attachments"></div><button aria-label="Send" disabled>Send</button></form>
<script>
window.sendCount=0;document.querySelector('[aria-label="Send"]').onclick=()=>window.sendCount++;
document.querySelector('input').onchange=e=>{
 let files=[...e.target.files];
 if(REVERSE) files.reverse();
 files.forEach((f,i)=>{
  const g=document.createElement('div');g.setAttribute('role','group');g.setAttribute('aria-label',f.name);
  const action=document.createElement('span');action.setAttribute('data-default-action','true');
  const b=document.createElement('button');b.type='button';b.setAttribute('aria-label',f.name);b.textContent=f.name;
  if(WAITING) b.className='cursor-wait';
  action.append(b);g.append(action);
  const r=document.createElement('button');r.type='button';r.setAttribute('aria-label','Remove file '+(i+1)+': '+f.name);r.onclick=()=>g.remove();g.append(r);
  document.getElementById('attachments').append(g);
 });
};</script>'''.replace('REVERSE',str(reverse).lower()).replace('WAITING',str(waiting).lower()))

    async def test_two_references_upload_in_order_without_legacy_send_or_generation(self):
        await self.attachment_fixture()
        refs=image_files.snapshot_references(self.root/'run',[self.source,self.guide])
        attachments=await image_files.upload_references(self.browser,self.page,refs)
        self.assertEqual([a['displayed_filename'] for a in attachments],['source.png','guide.png'])
        self.assertEqual([r['dimensions'] for r in refs],[[128,128],[128,128]])
        self.assertEqual(await self.page.evaluate('window.sendCount'),0)
        await image_files.remove_probe_attachments(self.browser,self.page,attachments)
        self.assertEqual(await self.browser.attachment_tiles(self.page),[])

    async def image_attachment_fixture(self, *, complete=True):
        encoded=base64.b64encode(self.raw).decode('ascii')
        await self.html('''<form><div contenteditable="true" role="textbox" aria-label="Ask ChatGPT"></div>
<input type="file" multiple aria-label="Attach photos"><div data-composer-attachments></div>
<button type="button" aria-label="Send">Send</button></form><script>
window.sendCount=0;document.querySelector('[aria-label="Send"]').onclick=()=>window.sendCount++;
document.querySelector('input').onchange=e=>{
 const container=document.querySelector('[data-composer-attachments]');container.replaceChildren();
 const tiles=[...e.target.files].map(file=>{
   const tile=document.createElement('div');tile.setAttribute('role','button');tile.setAttribute('aria-label',file.name);
   const img=document.createElement('img');img.alt=file.name;img.src='data:image/png;base64,IMAGE';tile.append(img);
   const remove=document.createElement('button');remove.type='button';remove.setAttribute('aria-label','Remove '+file.name);
   remove.onclick=()=>tile.remove();tile.append(remove);
   const progress=document.createElement('span');progress.setAttribute('role','progressbar');
   progress.setAttribute('aria-label','Uploading '+file.name);tile.append(progress);container.append(tile);
   return {tile,img,remove,progress,file};
 });
 window.finishUploads=()=>tiles.forEach(({tile,img,remove,progress,file})=>{
   const name=file.name.replace(/\\.png$/,'(2).png');tile.setAttribute('aria-label',name);
   img.alt=name;remove.setAttribute('aria-label','Remove '+name);progress.remove();
 });
 if(COMPLETE) setTimeout(window.finishUploads,100);
};</script>'''.replace('IMAGE',encoded).replace('COMPLETE',str(complete).lower()))

    async def test_current_image_tiles_wait_for_processing_and_collision_rename(self):
        await self.image_attachment_fixture()
        refs=image_files.snapshot_references(self.root/'run',[self.source,self.guide])
        attachments=await image_files.upload_references(self.browser,self.page,refs)
        self.assertEqual([a['displayed_filename'] for a in attachments],['source(2).png','guide(2).png'])
        self.assertEqual(await self.page.evaluate('window.sendCount'),0)
        await image_files.remove_probe_attachments(self.browser,self.page,attachments)
        self.assertEqual(await self.browser.attachment_tiles(self.page),[])

    async def test_current_image_preview_and_enabled_send_do_not_prove_ready(self):
        await self.image_attachment_fixture(complete=False)
        self.browser.upload_timeout_seconds=0
        refs=image_files.snapshot_references(self.root/'run',[self.source,self.guide])
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.upload_references(self.browser,self.page,refs)
        self.assertEqual(error.exception.code,'upload_not_ready')
        self.assertTrue(await self.page.get_by_role('button',name='Send',exact=True).is_enabled())
        self.assertFalse(any(t['ready'] for t in await self.browser.attachment_tiles(self.page)))
        self.assertEqual(await self.page.evaluate('window.sendCount'),0)

    async def test_failed_upload_diagnostic_contains_readiness_without_prompt_or_image_bytes(self):
        await self.image_attachment_fixture(complete=False)
        await self.page.locator(image_files.EDITOR).fill('PRIVATE-DRAFT-NOT-FOR-LOGS')
        self.browser.upload_timeout_seconds=0
        refs=image_files.snapshot_references(self.root/'run',[self.source,self.guide])
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.upload_references(self.browser,self.page,refs)
        evidence=error.exception.diagnostic['upload']
        self.assertEqual(evidence['expected_names'],['source.png','guide.png'])
        self.assertTrue(evidence['busy'])
        self.assertTrue(evidence['send_enabled'])
        self.assertEqual(len(evidence['tiles']),2)
        self.assertNotIn('PRIVATE-DRAFT',json.dumps(evidence))
        self.assertNotIn('data:image',json.dumps(evidence))
        self.assertEqual(await self.page.evaluate('window.sendCount'),0)

    async def test_unknown_remove_label_outside_attachment_wrapper_blocks_upload(self):
        await self.html('<form><div contenteditable="true" role="textbox" aria-label="Ask ChatGPT"></div>'
                        '<div><img alt="Other.png"><button aria-label="Remove Other.png">Remove</button></div></form>')
        with self.assertRaises(headless.ResearchError) as error:
            await self.browser.empty_attachment_inventory(self.page)
        self.assertEqual(error.exception.code,'composer_attachments_present')
        self.assertEqual(await self.page.locator('img').count(),1)

    async def test_obscured_send_stays_before_intent_and_can_be_reconciled_as_unsent(self):
        await self.attachment_fixture()
        await self.page.evaluate('''() => {
          document.querySelector('[aria-label="Send"]').disabled=false;
          const overlay=document.createElement('div');overlay.style='position:fixed;inset:0;background:#999';
          document.body.append(overlay);
        }''')
        receipt=headless.Receipt(self.root/'covered/receipt.json')
        receipt.create('covered','Create one square image')
        editor,scope,_=await headless.composer_state(self.page)
        required=headless.required
        async def bounded_required(locator,code,**kwargs):
            return await required(locator,code,timeout=0.1,**kwargs)
        with patch.object(headless,'required',bounded_required):
            with self.assertRaises(headless.ResearchError) as error:
                await headless.submit_once(self.page,editor,scope,receipt,browser=self.browser)
        self.assertEqual(error.exception.code,'send_unavailable')
        self.assertEqual(receipt.data['send_state'],'not_sent')
        self.assertEqual(receipt.data['send_clicks'],0)
        self.assertEqual(await self.page.evaluate('window.sendCount'),0)

    async def test_unrecognized_image_attachment_is_preserved_and_blocks_upload(self):
        await self.html('<form><div contenteditable="true" role="textbox" aria-label="Ask ChatGPT"></div>'
                        '<div data-composer-attachments><img alt="Unrelated.png" src="data:image/png;base64,'+
                        base64.b64encode(self.raw).decode('ascii')+'"></div></form>')
        with self.assertRaises(headless.ResearchError) as error:
            await self.browser.empty_attachment_inventory(self.page)
        self.assertEqual(error.exception.code,'composer_attachments_present')
        self.assertEqual(await self.page.locator('img').count(),1)

    async def test_reversed_ui_order_is_rejected_without_send(self):
        await self.attachment_fixture(reverse=True)
        refs=image_files.snapshot_references(self.root/'run',[self.source,self.guide])
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.upload_references(self.browser,self.page,refs)
        self.assertEqual(error.exception.code,'reference_order_unverified')
        self.assertEqual(await self.page.evaluate('window.sendCount'),0)

    async def test_multiple_file_inputs_use_only_owned_attach_photos(self):
        await self.attachment_fixture()
        await self.page.evaluate('''() => {
          document.querySelector('input').setAttribute('aria-label','Attach photos');
          for(const name of ['Attach files','Attach photos or videos']) {
            const input=document.createElement('input');input.type='file';input.multiple=true;
            input.setAttribute('aria-label',name);document.querySelector('form').prepend(input);
          }
          const outside=document.createElement('input');outside.type='file';outside.multiple=true;
          outside.setAttribute('aria-label','Upload files');document.body.prepend(outside);
        }''')
        refs=image_files.snapshot_references(self.root/'run',[self.source,self.guide])
        await image_files.upload_references(self.browser,self.page,refs)
        inventory=await self.page.locator('input[type=file]').evaluate_all(
            'es=>es.map(e=>({name:e.getAttribute("aria-label"),files:e.files.length}))')
        self.assertEqual({i['name']:i['files'] for i in inventory},
                         {'Upload files':0,'Attach photos or videos':0,'Attach files':0,'Attach photos':2})
        self.assertEqual(await self.page.evaluate('window.sendCount'),0)

    async def test_snapshot_change_after_decode_cannot_change_uploaded_bytes(self):
        await self.attachment_fixture()
        refs=image_files.snapshot_references(self.root/'run',[self.source])
        decode=image_files.decode_image
        async def decode_then_change(page,raw,mime):
            info=await decode(page,raw,mime)
            Path(refs[0]['snapshot_path']).write_bytes(b'changed after hash verification')
            return info
        with patch.object(image_files,'decode_image',decode_then_change):
            await image_files.upload_references(self.browser,self.page,refs)
        actual=await self.page.locator('input[type=file]').evaluate('''async e=>{
          const hash=await crypto.subtle.digest('SHA-256',await e.files[0].arrayBuffer());
          return [...new Uint8Array(hash)].map(b=>b.toString(16).padStart(2,'0')).join('');
        }''')
        self.assertEqual(actual,refs[0]['sha256'])
        self.assertEqual(await self.page.evaluate('window.sendCount'),0)

    async def test_processing_reference_cannot_become_ready_from_enabled_send(self):
        await self.attachment_fixture(waiting=True)
        self.browser.upload_timeout_seconds=0
        refs=image_files.snapshot_references(self.root/'run',[self.source,self.guide])
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.upload_references(self.browser,self.page,refs)
        self.assertEqual(error.exception.code,'upload_not_ready')
        self.assertEqual(await self.page.evaluate('window.sendCount'),0)

    async def test_invalid_image_does_not_reach_upload(self):
        await self.attachment_fixture()
        # 1) not an image at all: rejected when the reference is snapshotted, before any receipt or browser step
        self.source.write_bytes(b'not a PNG')
        with self.assertRaises(headless.ResearchError) as error:
            image_files.snapshot_references(self.root/'run',[self.source])
        self.assertEqual(error.exception.code,'reference_image_invalid')
        # 2) a file the structural check accepts but the browser cannot decode: the decode still stops it before upload
        self.source.write_bytes(self.raw)
        refs=image_files.snapshot_references(self.root/'run2',[self.source])
        async def cannot_decode(*_,**__):
            raise headless.ResearchError('image_decode_timeout','The browser did not decode the image in time')
        with patch.object(image_files,'decode_image',cannot_decode),patch.object(self.browser,'upload',AsyncMock()) as upload:
            with self.assertRaises(headless.ResearchError) as error:
                await image_files.upload_references(self.browser,self.page,refs)
            self.assertEqual(error.exception.code,'image_decode_timeout')
            upload.assert_not_awaited()

    async def download_fixture(self, *, different=False):
        original=base64.b64encode(self.raw).decode('ascii')
        output=original
        if different:
            output=await self.page.evaluate("() => {const c=document.createElement('canvas');c.width=128;c.height=128;const x=c.getContext('2d');x.fillStyle='red';x.fillRect(0,0,128,128);return c.toDataURL().split(',')[1]}")
        await self.html(f'''<main><button aria-label="Owned.png">Owned.png</button>
<img alt="Owned.png" width="300" src="data:image/png;base64,{original}">
<button aria-label="Download file" onclick="const a=document.createElement('a');a.href='data:image/png;base64,{output}';a.download='Owned.png';a.click();">Download</button></main>''')
        await self.page.wait_for_function('() => document.images[0].complete&&document.images[0].naturalWidth===128')
        fingerprint=await image_files.decode_image(self.page,self.raw,'image/png')
        receipt=headless.Receipt(self.root/'download/receipt.json')
        receipt.create('download','Create a blue square')
        receipt.update(send_state='confirmed',send_clicks=1,generation_state='completed',library_verified=True,
                       library_card_names=['Owned.png'],image_dimensions=[[128,128]],
                       image_fingerprints=[fingerprint],download_file=str(self.root/'native.png'))
        return receipt

    async def test_download_keeps_original_file_bytes_and_records_dimensions(self):
        receipt=await self.download_fixture()
        await image_files.download_owned(self.page,receipt,headless.VIEWER_FINGERPRINTS)
        self.assertEqual((self.root/'native.png').read_bytes(),self.raw)
        self.assertEqual(receipt.data['original_file']['dimensions'],[128,128])
        self.assertEqual(receipt.data['original_file']['sha256'],hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(receipt.data['send_clicks'],1)
        self.assertTrue(receipt.data['downloaded'])

    async def test_downloaded_different_image_is_not_published_as_owned_result(self):
        receipt=await self.download_fixture(different=True)
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.download_owned(self.page,receipt,headless.VIEWER_FINGERPRINTS)
        self.assertEqual(error.exception.code,'download_identity_unverified')
        self.assertFalse((self.root/'native.png').exists())
        self.assertFalse(receipt.data['downloaded'])
        self.assertEqual(list(self.root.glob('.chatgpt-original-*')),[])

    async def test_download_does_not_overwrite_unrelated_file(self):
        receipt=await self.download_fixture()
        target=self.root/'native.png'
        target.write_bytes(b'previous artifact')
        with self.assertRaises(headless.ResearchError):
            await image_files.download_owned(self.page,receipt,headless.VIEWER_FINGERPRINTS)
        self.assertEqual(target.read_bytes(),b'previous artifact')

    async def test_download_stem_records_actual_native_filename_and_exact_bytes(self):
        receipt=await self.download_fixture()
        receipt.update(download_file=None,download_stem=str(self.root/'native'))
        await image_files.download_owned(self.page,receipt,headless.VIEWER_FINGERPRINTS)
        self.assertEqual(receipt.data['download_file'],str(self.root/'native.png'))
        self.assertEqual((self.root/'native.png').read_bytes(),self.raw)
        self.assertTrue(receipt.data['downloaded'])

    async def test_existing_file_for_output_stem_is_preserved_before_download(self):
        receipt=await self.download_fixture()
        receipt.update(download_file=None,download_stem=str(self.root/'native'))
        (self.root/'native.png').write_bytes(b'existing result')
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.download_owned(self.page,receipt,headless.VIEWER_FINGERPRINTS)
        self.assertEqual(error.exception.code,'download_target_exists')
        self.assertEqual((self.root/'native.png').read_bytes(),b'existing result')
        self.assertNotEqual(receipt.data.get('download_state'),'intent')

    async def test_crash_after_file_publication_reconciles_without_new_download(self):
        receipt=await self.download_fixture()
        update=receipt.update
        def interrupt_completion(**fields):
            if fields.get('download_state')=='completed':
                raise OSError('simulated interruption after publication')
            return update(**fields)
        with patch.object(receipt,'update',interrupt_completion):
            with self.assertRaises(OSError):
                await image_files.download_owned(self.page,receipt,headless.VIEWER_FINGERPRINTS)
        retained=headless.Receipt(receipt.path)
        self.assertEqual(retained.data['download_state'],'publishing')
        await self.page.close()
        await image_files.download_owned(self.page,retained,headless.VIEWER_FINGERPRINTS)
        self.assertTrue(retained.data['downloaded'])
        self.assertEqual((self.root/'native.png').read_bytes(),self.raw)

    async def test_default_browser_rejects_original_download(self):
        await self.browser.close()
        await self.browser.start(headless=True)
        self.page=await self.browser.context.new_page()
        receipt=await self.download_fixture()
        with self.assertRaises(headless.ResearchError) as error:
            await image_files.download_owned(self.page,receipt,headless.VIEWER_FINGERPRINTS)
        self.assertEqual(error.exception.code,'download_failed')
        self.assertFalse((self.root/'native.png').exists())


if __name__ == '__main__':
    unittest.main()
