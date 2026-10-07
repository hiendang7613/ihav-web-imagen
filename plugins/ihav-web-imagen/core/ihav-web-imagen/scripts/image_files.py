"""Reference uploads and requested original downloads through rendered browser UI."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import struct
import tempfile
import zlib
from pathlib import Path

from chatgpt_web.browser import Browser as ResearchBrowser, first_visible, required
from chatgpt_web.core import ResearchError, atomic_write

EDITOR = '#prompt-textarea,[contenteditable="true"][role="textbox"][aria-label="Ask ChatGPT"]'
MIMES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024

# Which browser a run uses. CloakBrowser (the runtime's own profile) is the default; `chrome` is plain Google Chrome in a
# profile of its own: never the user's everyday Chrome profile, never a copy of one, and no anti-detection options.
BROWSER_KINDS = ('cloakbrowser', 'chrome')
DEFAULT_BROWSER = 'cloakbrowser'
CHROME_PROFILE = 'chrome-profile'
CHROME_PATHS = ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                '~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/opt/google/chrome/chrome')


def chrome_binary():
    """The Google Chrome executable, or None. IHAV_WEB_IMAGEN_CHROME names one explicitly and is never second-guessed."""
    named = os.environ.get('IHAV_WEB_IMAGEN_CHROME')
    candidates = (named,) if named else CHROME_PATHS
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None


async def close_browser(browser, primary_error):
    """Keep the operation outcome and expose a secondary cleanup failure."""
    try:
        await browser.close()
    except Exception as cleanup:
        if primary_error is None:
            raise
        error_code = getattr(cleanup, 'code', type(cleanup).__name__)
        existing = getattr(primary_error, 'diagnostic', None)
        diagnostic = dict(existing) if isinstance(existing, dict) else ({} if existing is None else {'operation': existing})
        diagnostic['browser_cleanup'] = {'state': 'failed', 'error': error_code}
        primary_error.diagnostic = diagnostic
        primary_error.add_note(f'browser_cleanup_failed: {error_code}')


async def start_playwright():
    from playwright.async_api import async_playwright
    return await async_playwright().start()

DECODE_IMAGE = r'''async ({data,mime}) => {
 const img=new Image();
 await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=()=>reject(new Error('image_decode_failed'));img.src='data:'+mime+';base64,'+data;});
 const canvas=document.createElement('canvas');canvas.width=32;canvas.height=32;
 const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0,32,32);
 const hash=await crypto.subtle.digest('SHA-256',ctx.getImageData(0,0,32,32).data);
 return {width:img.naturalWidth,height:img.naturalHeight,pixel_sha256:[...new Uint8Array(hash)].map(b=>b.toString(16).padStart(2,'0')).join('')};
}'''


class Browser(ResearchBrowser):
    """The vendored adapter's lifecycle; downloads are enabled only for an explicit image request."""

    async def composer_scope(self, page):
        editor = await required(page.locator(EDITOR), 'composer_unavailable')
        scope = editor.locator('xpath=ancestor::form[1]')
        if not await scope.count():
            scope = editor.locator('xpath=ancestor::*[@data-composer-body][1]')
        if await scope.count() != 1:
            raise ResearchError('composer_scope_unavailable', 'Cannot identify the owned composer')
        return scope

    async def attachment_tiles(self, page):
        scope = await self.composer_scope(page)
        return await scope.evaluate(r'''root=>{
          const visible=e=>!!(e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
          const busy=e=>[...e.querySelectorAll('[role="progressbar"],[aria-busy="true"],[role="alert"],.animate-spin')].some(visible);
          const tiles=[],covered=new Set();
          for(const control of root.querySelectorAll('button[aria-label^="Remove "]')) {
            if(!visible(control)) continue;
            const label=control.getAttribute('aria-label');
            if(label==='Remove Create image') continue;
            const container=control.closest('[data-composer-attachments]');
            const legacy=/^Remove file [1-9]\d*: (.+)$/.exec(label);
            if(!container&&!/^Remove (?:file|attachment)\b/.test(label)) {
              tiles.push({name:null,ready:false,reason:'unrecognized_removal_control',remove_label:label});
              continue;
            }
            if(legacy) {
              const name=legacy[1],group=control.closest('[role="group"]');
              const actions=group?[...group.querySelectorAll('[data-default-action="true"] button')]
                .filter(b=>b.getAttribute('aria-label')===name):[];
              const ready=group?.getAttribute('aria-label')===name&&actions.length===1&&
                !actions[0].disabled&&!actions[0].classList.contains('cursor-wait')&&
                getComputedStyle(actions[0]).cursor!=='wait'&&!busy(group);
              if(group) for(const img of group.querySelectorAll('img')) covered.add(img);
              tiles.push({name,ready:Boolean(ready),evidence:'legacy_file_control',remove_label:label});
              continue;
            }
            const name=/^Remove (.+)$/.exec(label)?.[1];
            const tile=control.parentElement?.closest('[role="button"][aria-label]');
            const images=tile?[...tile.querySelectorAll('img')].filter(visible):[];
            const ready=Boolean(container&&tile&&container.contains(tile)&&tile.getAttribute('aria-label')===name&&
              images.length===1&&images[0].alt===name&&images[0].complete&&
              images[0].naturalWidth>=1&&images[0].naturalHeight>=1&&!control.disabled&&
              control.getAttribute('aria-disabled')!=='true'&&tile.getAttribute('aria-disabled')!=='true'&&
              !tile.classList.contains('cursor-wait')&&getComputedStyle(tile).cursor!=='wait'&&!busy(tile));
            for(const img of images) covered.add(img);
            tiles.push({name:container&&tile?name:null,ready,evidence:'image_attachment_control',remove_label:label});
          }
          // An image with unknown markup is still an attachment, not an empty draft.
          for(const img of root.querySelectorAll('[data-composer-attachments] img')) {
            if(visible(img)&&!covered.has(img)) tiles.push({name:null,ready:false,reason:'unrecognized_image_attachment'});
          }
          return tiles;
        }''')

    async def upload_diagnostic(self, page, paths, before=None):
        scope = await self.composer_scope(page)
        state = await scope.evaluate(r'''root=>{
          const visible=e=>!!(e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
          const send=[...root.querySelectorAll('button')].filter(visible)
            .filter(e=>['Send','Send prompt','Send message'].includes(e.getAttribute('aria-label'))||e.getAttribute('data-testid')==='send-button');
          return {button_labels:[...root.querySelectorAll('button')].filter(visible).map(e=>e.getAttribute('aria-label')).filter(Boolean),
            image_count:[...root.querySelectorAll('img')].filter(visible).length,
            file_input_labels:[...root.querySelectorAll('input[type="file"]')].map(e=>e.getAttribute('aria-label')),
            busy:[...root.querySelectorAll('[role="progressbar"],[aria-busy="true"]')].some(visible),
            send_count:send.length,send_enabled:send.length===1&&!send[0].disabled&&send[0].getAttribute('aria-disabled')!=='true'};
        }''')
        return {'expected_names':[p.name for p in paths], 'tiles':await self.attachment_tiles(page), **state}

    unrecognized_grace_seconds = 10

    async def upload(self, page, paths, *, payloads=None):
        try:
            return await self._upload(page, paths, payloads=payloads)
        except Exception as exc:
            try:
                diagnostic = {'upload':await self.upload_diagnostic(page, paths)}
            except Exception:
                diagnostic = {'upload':{'evidence_unavailable':True}}
            if isinstance(exc, ResearchError):
                exc.diagnostic = diagnostic
                raise
            error = ResearchError('upload_control_failed', 'An upload UI action failed before Send')
            error.diagnostic = diagnostic
            raise error from exc

    async def _upload(self, page, paths, *, payloads=None):
        if not paths:
            return []
        if len(paths) > 2:
            raise ResearchError('reference_inventory_invalid', 'At most two image references are supported')
        await self.empty_attachment_inventory(page)
        scope = await self.composer_scope(page)
        file_input = scope.locator('input[type="file"][aria-label="Attach photos"]')
        if not await file_input.count():
            # Older composers have one file input inside the owned form.
            file_input = scope.locator('input[type="file"]')
        if await file_input.count() != 1:
            raise ResearchError('upload_unavailable', 'The fresh chat does not have one unambiguous file input')
        await file_input.set_input_files(payloads if payloads is not None else [str(p) for p in paths])
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.upload_timeout_seconds
        unnamed_since = None
        while True:
            if await first_visible(scope.locator('[role="alert"]')):
                raise ResearchError('upload_failed', 'The composer reported an attachment error')
            tiles = await self.attachment_tiles(page)
            busy = await first_visible(scope.locator('[role="progressbar"], [aria-busy="true"]'))
            # A tile can show its thumbnail before its controls render. Wait for it; only markup that stays
            # unidentifiable is unsupported.
            # The grace runs only while nothing is busy: a thumbnail whose controls are still missing during an active upload
            # (progressbar) is the normal transition and is bounded by the upload deadline instead.
            unnamed = any(t['name'] is None for t in tiles) and not busy
            unnamed_since = (unnamed_since if unnamed_since is not None else loop.time()) if unnamed else None
            if unnamed and loop.time() - unnamed_since >= self.unrecognized_grace_seconds:
                raise ResearchError('attachment_markup_unrecognized', 'An attachment cannot be identified by supported UI controls')
            if not unnamed and len(tiles) > len(paths):
                raise ResearchError('upload_identity_unverified', 'Unexpected attachments were preserved')
            if len(tiles) == len(paths) and all(t['ready'] for t in tiles) and not busy:
                names = [t['name'] for t in tiles]
                attachments = []
                for path in paths:
                    candidates = displayed_candidates(path, names)
                    if len(candidates) != 1:
                        raise ResearchError('upload_identity_unverified', 'Ready file names do not identify each reference uniquely')
                    attachments.append({'filename':path.name,'displayed_filename':candidates[0],
                                        'evidence':'owned_ready_attachment_control'})
                await validate_attachment_order(self, page, attachments)
                return attachments
            if asyncio.get_running_loop().time() >= deadline:
                raise ResearchError('upload_not_ready', 'References did not become ready before the upload deadline')
            await asyncio.sleep(0.5)


class ChromeBrowser(Browser):
    """Plain Google Chrome through Playwright, in `<state>/chrome-profile` (a profile of its own, signed in once with `login`).

    The launch uses Playwright's defaults and nothing else: no anti-detection arguments. If ChatGPT shows a verification
    challenge the run stops before the Send (human_verification_required) instead of working around it.
    """

    playwright = None

    async def start(self, *, headless=None, accept_downloads=False):
        binary = chrome_binary()
        if binary is None:
            raise ResearchError('browser_binary_missing', 'Google Chrome was not found (set IHAV_WEB_IMAGEN_CHROME to its executable)')
        profile = self.state / CHROME_PROFILE
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.playwright = await start_playwright()
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), executable_path=str(binary), headless=True if headless is None else headless,
                viewport={'width': 1440, 'height': 1100}, accept_downloads=accept_downloads)
        except BaseException as exc:
            await close_browser(self, exc)
            raise
        self.context.set_default_timeout(8000)
        self.context.set_default_navigation_timeout(45000)
        return self

    async def close(self):
        try:
            await super().close()
        finally:
            playwright, self.playwright = self.playwright, None
            if playwright is not None:
                async with asyncio.timeout(self.cleanup_timeout_seconds):
                    await playwright.stop()

    async def open_login_page(self):
        """Visible Chrome on ChatGPT for a manual sign-in. Nothing is sent."""
        await self.start(headless=False)
        page = await self.context.new_page()
        await page.goto('https://chatgpt.com/', wait_until='domcontentloaded')

    async def wait_until_closed(self):
        while any(not open_page.is_closed() for open_page in self.context.pages):
            await asyncio.sleep(1)


def displayed_candidates(path, names):
    """Names the UI may show for an uploaded file: the file name, or a dedupe rename `stem(N).ext` / `stem(YYYYMMDD-HHMMSS).ext`."""
    collision = re.compile(re.escape(path.stem) + r'\((?:[1-9]\d*|\d{8}-\d{6})\)' + re.escape(path.suffix))
    return [n for n in names if isinstance(n, str) and (n == path.name or collision.fullmatch(n))]


def checked_bytes(path, maximum):
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if not raw or len(raw) > maximum:
        raise ResearchError('image_size_invalid', 'Image is empty or exceeds the local byte limit')
    return raw


PNG_CRITICAL = (b'IHDR', b'PLTE', b'IDAT', b'IEND')
JPEG_SOF = (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
JPEG_UNSUPPORTED_SOF = (0xC5, 0xC6, 0xC7, 0xCD, 0xCE, 0xCF)   # hierarchical/differential frames: Chromium and Pillow both reject them


def validate_image_structure(raw, mime):
    """Cheap structural check, so a truncated or garbage 'image' fails before any receipt exists. It is not a decode:
    the browser decode in `upload_references` stays authoritative (and also runs before any Send). Any parser failure is
    reported as `reference_image_invalid`, never as a raw exception."""
    def bad():
        raise ResearchError('reference_image_invalid', 'The reference is not a well-formed PNG, JPEG or WebP image')
    try:
        if mime == 'image/png':
            _check_png(raw, bad)
        elif mime == 'image/jpeg':
            _check_jpeg(raw, bad)
        elif mime == 'image/webp':
            if len(raw) < 20 or raw[:4] != b'RIFF' or raw[8:12] != b'WEBP' or raw[12:16] not in (b'VP8 ', b'VP8L', b'VP8X'):
                bad()
            if struct.unpack('<I', raw[4:8])[0] + 8 > len(raw):       # truncated
                bad()
        else:
            bad()
    except (struct.error, IndexError, ValueError, zlib.error):
        bad()


def _check_png(raw, bad):
    """Check the PNG chunk sequence and the CRC of every chunk, without inflating image data.

    Unknown critical chunks and illegal IHDR/PLTE/IDAT ordering are rejected. A bad CRC on any chunk, ancillary ones
    included, marks a damaged file: Chromium would skip a damaged ancillary chunk (tEXt, iCCP, tRNS, eXIf...) while Pillow
    rejects the whole file, so the same bytes mean different things to different decoders.
    """
    if len(raw) < 33 or raw[:8] != b'\x89PNG\r\n\x1a\n':
        bad()
    position, first, idat, idat_closed, plte = 8, True, False, False, False
    color_type = bit_depth = None
    while True:
        if position + 12 > len(raw):
            bad()                                                    # no room for another chunk: IEND never came
        length = struct.unpack('>I', raw[position:position + 4])[0]
        kind = raw[position + 4:position + 8]
        end = position + 12 + length
        if length > 0x7FFFFFFF or end > len(raw):
            bad()
        if any(byte not in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz' for byte in kind) or kind[2] & 0x20:
            bad()                                                     # non-letters or the reserved lowercase bit
        if first:
            if kind != b'IHDR' or length != 13:
                bad()
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                '>IIBBBBB', raw[position + 8:position + 21])
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if (not 1 <= width <= 0x7FFFFFFF or not 1 <= height <= 0x7FFFFFFF or bit_depth not in depths.get(color_type, ())
                    or compression != 0 or filter_method != 0 or interlace not in (0, 1)):
                bad()                                                 # Chromium and Pillow both reject these header values
            first = False
        elif kind == b'IHDR':
            bad()                                                     # IHDR is unique and first
        if kind not in PNG_CRITICAL and not (kind[0] & 0x20):
            bad()                                                     # this checker cannot interpret unknown critical chunks
        if kind == b'PLTE':
            if plte or idat or (color_type == 3 and length // 3 > (1 << bit_depth)):
                bad()                                                 # a palette larger than the bit depth can index: Pillow rejects it
            plte = True
        if kind == b'IDAT':
            if idat_closed:
                bad()                                                 # IDAT chunks form one consecutive sequence
            idat = True
        elif idat:
            idat_closed = True
        stored = struct.unpack('>I', raw[end - 4:end])[0]
        if stored != zlib.crc32(raw[position + 4:end - 4]) & 0xFFFFFFFF:
            bad()                                                     # any damaged chunk: decoders disagree on what it then means
        if kind == b'IEND':
            if length != 0 or not idat:
                bad()
            return
        position = end


def _check_jpeg(raw, bad):
    """Check marker/segment bounds through at least one non-empty scan; entropy is not decoded here.

    A rule is here because a real decoder rejects the input (Chromium, Pillow: see tests/decoder_differential.json) or because
    content is visibly missing (no scan data), never for spec strictness alone: duplicate or all-zero component ids and
    DNL segments are tolerated.
    """
    if raw[:2] != b'\xff\xd8':
        bad()
    index = 2
    frame_marker, frame_components = None, []
    scans = 0
    while index < len(raw):
        if raw[index] != 0xFF:
            bad()
        marker_start = index
        while index < len(raw) and raw[index] == 0xFF:
            index += 1                                             # marker fill bytes
        if index >= len(raw):
            bad()
        marker = raw[index]
        index += 1
        if marker == 0xD9:                                          # EOI must follow a frame and a non-empty scan
            if frame_marker is None or scans == 0:
                bad()
            return
        if marker == 0xD8 or marker == 0x00 or 0xD0 <= marker <= 0xD7:
            bad()                                                     # SOI/stuffed/restart markers are invalid here
        if marker == 0x01:
            continue
        if index + 2 > len(raw):
            bad()
        length = struct.unpack('>H', raw[index:index + 2])[0]
        if length < 2 or index + length > len(raw):
            bad()
        segment_end = index + length
        if marker in JPEG_SOF:
            if frame_marker is not None:
                bad()
            if length < 11:                                          # precision 1 + height 2 + width 2 + components 1 + >= 1 x 3
                bad()
            precision = raw[index + 2]
            height, width = struct.unpack('>HH', raw[index + 3:index + 7])
            components = raw[index + 7]
            lossless = marker in {0xC3, 0xCB}
            precision_ok = (2 <= precision <= 16) if lossless else (precision == 8 if marker == 0xC0 else precision in (8, 12))
            # Height 0 would mean "given by a DNL segment": neither Chromium nor Pillow decodes such a frame (measured), so it is
            # damage here, and DNL segments need no logic of their own (they are skipped like any other segment).
            # Component count: T.81 allows 2..255 for most processes, but Chromium and Pillow both reject every count other than
            # 1 (grey), 3 (colour) and 4 (CMYK/YCCK), on genuine baseline and extended files (tests/decoder_differential.json).
            if (marker in JPEG_UNSUPPORTED_SOF or not precision_ok or not width or not height or components not in (1, 3, 4)
                    or length != 8 + 3 * components):
                bad()
            frame_components = [raw[index + 8 + 3 * offset] for offset in range(components)]
            frame_marker = marker
        elif marker == 0xDA:                                         # SOS
            if frame_marker is None or length < 8:
                bad()
            components = raw[index + 2]
            if not 1 <= components <= 4 or length != 6 + 2 * components:
                bad()
            scan_components = [raw[index + 3 + 2 * offset] for offset in range(components)]
            if not set(scan_components) <= set(frame_components):
                bad()                                                 # a scan selects a component the frame does not have
            if len(set(frame_components)) == len(frame_components):
                # With unique frame ids a repeated or out-of-order selector is rejected by both Chromium and Pillow (measured).
                # Frames whose ids repeat (writers that give every component id 0 exist) are fixed up by decoders: not checked.
                positions = [frame_components.index(component) for component in scan_components]
                if len(set(scan_components)) != components or positions != sorted(positions):
                    bad()
            index = segment_end
            scan_data = False
            while True:
                # Jump from one 0xFF to the next: entropy data is most of the file, a per-byte Python loop cost ~65 ms/MB.
                found = raw.find(b'\xff', index)
                if found < 0:
                    bad()                                                 # entropy data ended without another marker
                if found > index:
                    scan_data = True
                marker_start = index = found
                marker_index = index + 1
                while marker_index < len(raw) and raw[marker_index] == 0xFF:
                    marker_index += 1
                if marker_index >= len(raw):
                    bad()
                scan_marker = raw[marker_index]
                if scan_marker == 0x00:                              # byte-stuffed 0xFF is scan data
                    scan_data = True
                    index = marker_index + 1
                    continue
                if 0xD0 <= scan_marker <= 0xD7:                     # restart interval marker
                    index = marker_index + 1
                    continue
                if not scan_data:
                    bad()
                scans += 1
                index = marker_start                             # process the next marker in the outer loop
                break
            continue
        index = segment_end
    bad()                                                            # no complete EOI


def validate_reference_paths(paths):
    try:
        paths = [Path(p).resolve(strict=True) for p in paths]
    except OSError as exc:
        raise ResearchError('reference_file_invalid', 'A reference file is missing or inaccessible') from exc
    if len(paths) > 2 or len({p.name for p in paths}) != len(paths):
        raise ResearchError('reference_inventory_invalid', 'Use at most two distinctly named images, in prompt order')
    for path in paths:
        if not path.is_file():
            raise ResearchError('reference_file_invalid', 'References must be ordinary image files')
        if path.suffix.lower() not in MIMES:
            raise ResearchError('reference_format_unsupported', 'Use PNG, JPEG or WebP references')
        if not 0 < path.stat().st_size <= MAX_REFERENCE_BYTES:
            raise ResearchError('image_size_invalid', 'Image is empty or exceeds the local byte limit')
        validate_image_structure(checked_bytes(path, MAX_REFERENCE_BYTES), MIMES[path.suffix.lower()])
    return paths


def snapshot_references(directory, paths):
    paths = validate_reference_paths(paths)
    prepared = []
    for path in paths:
        mime = MIMES.get(path.suffix.lower())
        if mime is None:
            raise ResearchError('reference_format_unsupported', 'Use PNG, JPEG or WebP references')
        raw = checked_bytes(path, MAX_REFERENCE_BYTES)
        prepared.append((path, mime, raw))
    references = []
    for number, (path, mime, raw) in enumerate(prepared, 1):
        target = directory / 'references' / path.name
        if target.exists():
            raise ResearchError('reference_snapshot_exists', 'An existing reference snapshot was preserved')
        atomic_write(target, raw)
        references.append({'order':number,'source_path':str(path),'snapshot_path':str(target),
                           'filename':path.name,'mime':mime,'sha256':hashlib.sha256(raw).hexdigest(),
                           'bytes':len(raw)})
    return references


def reference_bytes(reference):
    raw = checked_bytes(Path(reference['snapshot_path']), MAX_REFERENCE_BYTES)
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ResearchError('reference_snapshot_changed', 'The retained reference bytes no longer match this run')
    return raw


async def decode_image(page, raw, mime):
    try:
        async with asyncio.timeout(15):
            return await page.evaluate(DECODE_IMAGE, {'data':base64.b64encode(raw).decode('ascii'),'mime':mime})
    except TimeoutError as exc:
        raise ResearchError('image_decode_timeout', 'The browser did not decode the image in time') from exc


async def validate_attachment_order(browser, page, attachments):
    await browser.verify_attachment_inventory(page, attachments)
    names = [tile['name'] for tile in await browser.attachment_tiles(page)]
    if names != [item['displayed_filename'] for item in attachments]:
        raise ResearchError('reference_order_unverified', 'Visible attachments differ from the requested reference order')


async def upload_references(browser, page, references):
    await browser.empty_attachment_inventory(page)
    payloads = []
    for reference in references:
        raw = reference_bytes(reference)
        info = await decode_image(page, raw, reference['mime'])
        reference['dimensions'] = [info['width'], info['height']]
        payloads.append({'name':reference['filename'],'mimeType':reference['mime'],'buffer':raw})
    attachments = await browser.upload(page, [Path(r['snapshot_path']) for r in references], payloads=payloads)
    await validate_attachment_order(browser, page, attachments)
    return attachments


async def remove_probe_attachments(browser, page, attachments):
    # Only the attachments added to this verified empty, task-owned composer.
    await validate_attachment_order(browser, page, attachments)
    for item in attachments:
        name = re.compile(r'^Remove (?:file [1-9]\d*: )?' + re.escape(item['displayed_filename']) + r'$')
        scope = await browser.composer_scope(page)
        control = scope.get_by_role('button', name=name)
        if await control.count() != 1:
            raise ResearchError('probe_cleanup_unverified', 'The owned attachment removal control is ambiguous')
        try:
            # Live UI (2026-09-30): the removal control ignores the pointer until its tile is hovered, and the
            # thumbnail covers it, so a plain click times out. Hover the tile as a user would, then click.
            tile = control.locator('xpath=ancestor::*[@role="button"][1]')
            if await tile.count():
                await tile.hover()
            await control.click()
        except ResearchError:
            raise
        except Exception as exc:
            error = ResearchError('probe_cleanup_failed', 'The owned attachment could not be removed through its control')
            error.diagnostic = {'cleanup': {'attachment': item['displayed_filename'], 'error': type(exc).__name__}}
            raise error from exc
    await browser.empty_attachment_inventory(page)


def reconcile_original(receipt):
    data = receipt.data
    saved = data.get('original_file', {})
    if data.get('download_file'):
        target = Path(data['download_file'])
    elif data.get('download_stem'):
        stem = Path(data['download_stem'])
        target = Path(saved['path']) if saved.get('path') else None
        if target is not None and (target.parent != stem.parent or target.stem != stem.name or target.suffix.lower() not in MIMES):
            raise ResearchError('download_identity_unverified', 'The retained file does not match the requested output stem')
        if target is None and any(stem.parent.glob(stem.name + '.*')):
            raise ResearchError('download_target_exists', 'An existing output file was preserved')
    else:
        raise ResearchError('download_not_requested', 'Original-file download needs an explicit destination')
    if target is not None and target.exists():
        if saved.get('path') == str(target) and saved.get('sha256') == hashlib.sha256(checked_bytes(target, MAX_OUTPUT_BYTES)).hexdigest():
            receipt.update(downloaded=True, download_state='completed', download_file=str(target))
            return True
        raise ResearchError('download_target_exists', 'An existing output file was preserved')
    return False


async def download_owned(library, receipt, viewer_script):
    data = receipt.data
    if not data.get('library_verified') or len(data.get('library_card_names', [])) != 1:
        raise ResearchError('download_identity_unverified', 'Download requires exactly one verified library result')
    if reconcile_original(receipt):
        return
    card_name = data['library_card_names'][0]
    card = library.get_by_role('button', name=card_name, exact=True)
    if await card.count() != 1:
        raise ResearchError('download_identity_unverified', 'Cannot identify the verified creation card uniquely')
    await card.click()
    expected = {i['pixel_sha256'] for i in data.get('image_fingerprints', []) if i.get('pixel_sha256')}
    if len(expected) != 1:
        raise ResearchError('download_identity_unverified', 'The owned turn lacks one rendered pixel identity')
    deadline = asyncio.get_running_loop().time() + 20
    while True:
        views = await library.evaluate(viewer_script, {'dimensions':data['image_dimensions'],'cardName':card_name})
        if any(v.get('pixel_sha256') in expected for v in views):
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise ResearchError('download_identity_unverified', 'The opened viewer does not match the owned turn')
        await asyncio.sleep(0.5)
    control = library.get_by_role('button', name='Download file', exact=True)
    if await control.count() != 1:
        raise ResearchError('download_control_unverified', 'The viewer download control is ambiguous')
    await publish_original(library, control, receipt, expected)


async def publish_original(page, control, receipt, expected):
    """Publish exact UI download bytes; both UI routes use the same recovery contract."""
    if len(expected) != 1:
        raise ResearchError('download_identity_unverified', 'One owned pixel identity is required')
    if reconcile_original(receipt):
        return
    data = receipt.data
    explicit = Path(data['download_file']) if data.get('download_file') else None
    stem = Path(data['download_stem']) if data.get('download_stem') else None
    parent = (explicit or stem).parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt.update(download_state='intent')
    async with page.expect_download(timeout=45000) as pending:
        await control.click()
    download = await pending.value
    if await download.failure():
        raise ResearchError('download_failed', 'The browser reported a failed download')
    mime = MIMES.get(Path(download.suggested_filename).suffix.lower())
    if mime is None:
        raise ResearchError('download_format_unsupported', 'The viewer did not return a supported image file')
    # An explicit destination must name the native format: an unknown or missing suffix would publish e.g. WebP bytes as `.txt`.
    if explicit is not None and MIMES.get(explicit.suffix.lower()) != mime:
        error = ResearchError('download_extension_mismatch', 'The requested file extension names another format than the native download')
        error.diagnostic = {'download': {'native_suffix': Path(download.suggested_filename).suffix.lower(),
                                         'requested_suffix': explicit.suffix.lower()}}
        raise error
    target = explicit or stem.with_suffix(Path(download.suggested_filename).suffix.lower())
    receipt.update(download_file=str(target))
    fd, temporary = tempfile.mkstemp(prefix='.chatgpt-original-', dir=target.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        await download.save_as(str(temporary))
        raw = checked_bytes(temporary, MAX_OUTPUT_BYTES)
        native = await decode_image(page, raw, mime)
        if native['pixel_sha256'] not in expected:
            raise ResearchError('download_identity_unverified', 'Downloaded raster differs from the owned viewer')
        temporary.chmod(0o600)
        original = {'path':str(target),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                    'mime':mime,'suggested_filename':download.suggested_filename,
                    'dimensions':[native['width'],native['height']],'pixel_sha256':native['pixel_sha256']}
        # Retain the identity before publication, so a crash after linking can
        # reconcile the exact existing file without another browser download.
        receipt.update(download_state='publishing', original_file=original)
        # Same-filesystem publication without overwriting a concurrent artifact.
        os.link(temporary, target)
        receipt.update(downloaded=True, download_state='completed', original_file=original)
    finally:
        temporary.unlink(missing_ok=True)
