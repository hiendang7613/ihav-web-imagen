"""Gemini image adapter: one image request through gemini.google.com in the signed-in CloakBrowser profile.

    gemini.py generate --run-id ID --prompt-file FILE --out DIR   # at most one Send; saves the original when Gemini offers it
    gemini.py resume --run-id ID                                 # read-only: reopen the saved chat, wait, save; never sends
    gemini.py status --run-id ID                                 # the receipt, no browser

Same contract as the ChatGPT route: a durable receipt with send_state=intent is written before the only click, and a run
with any send_state other than not_sent is never submitted again.

Evidence (2026-10-04, read-only survey of one signed-in Vietnamese-UI account): the editor is `.ql-editor[role=textbox]`;
"Uploads and tools" opens a menu whose menuitemcheckbox "Create image" turns the composer into image mode (a chip whose
button is "Deselect Image"); Send appears after text is typed. The result and download controls had NOT been observed
when this was written; they are matched by label in English or Vietnamese, and an ambiguous control stops the download
(never the request). Labels for other UI languages are not known.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headless  # noqa: E402
from chatgpt_web.core import DEFAULT_STATE, ResearchError  # noqa: E402
from image_files import MIMES, checked_bytes, decode_image  # noqa: E402

HOME = 'https://gemini.google.com/app'
EDITOR = '.ql-editor[role="textbox"]'
# English and Vietnamese labels seen or expected; extend only from a survey.
TOOLS = re.compile(r'^(Uploads and tools|Nội dung tải lên và công cụ)$')
CREATE_IMAGE = re.compile(r'^(Create image|Create images|Tạo hình ảnh)$')
IMAGE_CHIP = re.compile(r'^(Deselect Image|Deselect image|Bỏ chọn Hình ảnh)$')
SEND = re.compile(r'^(Send message|Gửi tin nhắn)$')
STOP = re.compile(r'^(Stop response|Stop generating|Dừng phản hồi|Dừng tạo)$')
DOWNLOAD = re.compile(r'(download full size|tải.*xuống.*kích thước đầy đủ|tải xuống hình ảnh có kích thước đầy đủ)', re.I)
RESPONSES = 'model-response'
RUN_ROOT = 'gemini-images'


def chat_url(value):
    parsed = urlparse(value or '')
    if parsed.scheme == 'https' and parsed.netloc == 'gemini.google.com' and re.fullmatch(r'/app/[a-f0-9]{8,}', parsed.path):
        return 'https://gemini.google.com' + parsed.path
    return None


def receipt_for(state, run_id):
    return headless.Receipt(state / RUN_ROOT / run_id / 'receipt.json')


async def one(locator, code, *, timeout=15.0):
    """Exactly one visible match within the timeout, else `code`. Never clicks."""
    deadline = time.monotonic() + timeout
    while True:
        count = await locator.count()
        if count == 1 and await locator.first.is_visible():
            return locator.first
        if time.monotonic() >= deadline:
            raise ResearchError(code, f'Expected one control, found {count}')
        await asyncio.sleep(0.25)


async def check_signed_in(page):
    if 'accounts.google.com' in page.url:
        raise ResearchError('signed_out', 'Gemini redirected to Google sign-in; run runtime.py login --site gemini')
    await one(page.locator(EDITOR), 'composer_unavailable', timeout=30)


async def image_mode(page):
    return await page.get_by_role('button', name=IMAGE_CHIP).count() == 1


async def select_image_tool(page):
    """Menu choice only: nothing is typed or sent."""
    if await image_mode(page):
        return
    await (await one(page.get_by_role('button', name=TOOLS), 'tools_menu_unavailable')).click()
    item = await one(page.locator('.cdk-overlay-container [role="menuitemcheckbox"]').filter(has_text=CREATE_IMAGE),
                     'image_tool_unavailable', timeout=8)
    await item.click()
    deadline = time.monotonic() + 8
    while not await image_mode(page):
        if time.monotonic() >= deadline:
            raise ResearchError('image_mode_unverified', 'Gemini did not show the image chip')
        await asyncio.sleep(0.25)


async def response_images(page):
    """Rendered images of the newest model response, largest first, with a pixel fingerprint (same rule as ChatGPT)."""
    return await page.evaluate(r'''async () => {
      const all=[...document.querySelectorAll('model-response')]; const last=all[all.length-1];
      if(!last) return {responses:0, images:[]};
      const imgs=[...last.querySelectorAll('img')].filter(e=>e.getClientRects().length&&e.complete&&e.naturalWidth>=256&&e.naturalHeight>=256);
      const out=[];
      for(const e of imgs){ let sha=null;
        try{const c=document.createElement('canvas');c.width=32;c.height=32;const x=c.getContext('2d');x.drawImage(e,0,0,32,32);
            const h=await crypto.subtle.digest('SHA-256',x.getImageData(0,0,32,32).data);sha=[...new Uint8Array(h)].map(b=>b.toString(16).padStart(2,'0')).join('');}catch{}
        out.push({width:e.naturalWidth,height:e.naturalHeight,pixel_sha256:sha}); }
      return {responses:all.length, images:out};
    }''')


async def submit(page, receipt):
    """Type the prompt in image mode and click Send once, after the durable intent."""
    if receipt.data['send_state'] != 'not_sent':
        raise ResearchError('resend_forbidden', 'An existing submission intent must be reconciled')
    prompt = receipt.data['prompt']
    editor = await one(page.locator(EDITOR), 'composer_unavailable')
    if headless.norm(await editor.inner_text()):
        raise ResearchError('unexpected_draft', 'The existing draft was preserved')
    await select_image_tool(page)
    before = (await response_images(page))['responses']
    await editor.click()
    await page.keyboard.insert_text(prompt)
    if headless.norm(await editor.inner_text()) != headless.norm(prompt):
        raise ResearchError('draft_mismatch', 'The editor did not accept the exact prompt')
    if not await image_mode(page):
        raise ResearchError('image_mode_unverified', 'Typing the prompt removed image mode')
    headless.mark(receipt, 'prompt_inserted')
    send = await one(page.get_by_role('button', name=SEND), 'send_unavailable', timeout=8)
    if await send.get_attribute('aria-disabled') == 'true' or not await send.is_enabled():
        raise ResearchError('send_not_ready', 'Gemini has not enabled Send')
    receipt.update(send_state='intent', intent_at=headless.utc_now(), send_clicks=1, responses_before=before)
    headless.mark(receipt, 'intent')
    try:
        await send.click(timeout=10000)
    except Exception:
        receipt.update(send_state='unknown', reason='send_click_uncertain', chat_url=chat_url(page.url))
        raise ResearchError('send_click_uncertain', 'Reconcile this run; never submit it again')


async def observe(page, receipt, *, wait_seconds):
    """Wait for the new response's image; confirm the send when a new response appears. Read-only."""
    before = receipt.data.get('responses_before', 0)
    deadline = time.monotonic() + wait_seconds
    stable, last = 0, None
    while True:
        url = chat_url(page.url)
        if url and receipt.data.get('chat_url') != url:
            receipt.update(chat_url=url)
        view = await response_images(page)
        if view['responses'] > before and receipt.data['send_state'] == 'intent':
            receipt.update(send_state='confirmed')
            headless.mark(receipt, 'turn_posted')
        generating = await page.get_by_role('button', name=STOP).count()
        images = view['images'] if view['responses'] > before or receipt.data.get('recovered') else []
        if images and not generating:
            key = [i['pixel_sha256'] for i in images]
            stable = stable + 1 if key == last else 0
            last = key
            if stable >= 2:
                receipt.update(generation_state='completed', image_dimensions=[[i['width'], i['height']] for i in images],
                               image_fingerprints=images)
                headless.mark(receipt, 'final_complete')
                return True
        if time.monotonic() >= deadline:
            if receipt.data['send_state'] == 'intent':
                receipt.update(send_state='unknown', reason='posted_prompt_unverified')
            return False
        await asyncio.sleep(1.0)


async def download(page, receipt):
    """Save the original through Gemini's own download control on the newest response's image; check its pixels."""
    if receipt.data.get('downloaded'):
        return
    expected = {i['pixel_sha256'] for i in receipt.data.get('image_fingerprints', []) if i.get('pixel_sha256')}
    if len(receipt.data.get('image_dimensions', [])) != 1 or len(expected) != 1:
        raise ResearchError('download_identity_unverified', 'The response does not show exactly one generated image')
    last = page.locator(RESPONSES).last
    await last.locator('img').last.hover()          # Gemini shows the image's controls on hover
    control = await one(last.get_by_role('button', name=DOWNLOAD), 'download_control_unverified', timeout=10)
    stem = Path(receipt.data['download_stem'])
    receipt.update(download_state='intent')
    async with page.expect_download(timeout=60000) as pending:
        await control.click()
    item = await pending.value
    if await item.failure():
        raise ResearchError('download_failed', 'The browser reported a failed download')
    suffix = Path(item.suggested_filename).suffix.lower()
    mime = MIMES.get(suffix)
    if mime is None:
        raise ResearchError('download_format_unsupported', 'Gemini did not return a supported image file')
    target = stem.with_suffix(suffix)
    if target.exists():
        raise ResearchError('download_target_exists', 'An existing output file was preserved')
    temporary = target.with_name('.' + target.name + '.part')
    await item.save_as(str(temporary))
    raw = checked_bytes(temporary, 64 * 1024 * 1024)
    native = await decode_image(page, raw, mime)
    pixel_match = native['pixel_sha256'] in expected
    temporary.rename(target)
    receipt.update(downloaded=True, download_state='completed', download_file=str(target),
                   original_file={'path': str(target), 'bytes': len(raw), 'mime': mime, 'suggested_filename': item.suggested_filename,
                                  'dimensions': [native['width'], native['height']], 'pixel_sha256': native['pixel_sha256'],
                                  'matches_rendered_preview': pixel_match})


async def run(args):
    state = args.state_dir
    receipt = receipt_for(state, args.run_id)
    if args.command == 'status':
        return receipt.summary() if receipt.data else {'error': 'run_not_found'}
    async with headless.session(state, accept_downloads=True) as browser:
        page = await browser.context.new_page()
        if args.command == 'generate':
            prompt = args.prompt_file.read_text().strip()
            receipt.create(args.run_id, prompt, download_stem=str((args.out.expanduser().resolve() / args.run_id)))
            receipt.update(route='gemini', provider='gemini')
            headless.mark(receipt, 'start')
            await page.goto(HOME, wait_until='domcontentloaded')
            await check_signed_in(page)
            await submit(page, receipt)
        else:
            if not receipt.data:
                raise ResearchError('run_not_found', 'No receipt for this run id')
            url = receipt.data.get('chat_url')
            if not url:
                raise ResearchError('chat_url_unknown', 'The run has no saved Gemini chat to observe; nothing will be sent')
            receipt.update(recovered=True)
            await page.goto(url, wait_until='domcontentloaded')
            await check_signed_in(page)
        if await observe(page, receipt, wait_seconds=args.wait_seconds):
            await download(page, receipt)
        return receipt.summary()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('generate', 'resume', 'status'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--prompt-file', type=Path)
    parser.add_argument('--out', type=Path, default=Path.cwd())
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--wait-seconds', type=float, default=300)
    args = parser.parse_args(argv)
    if not headless.RUN_ID.fullmatch(args.run_id):
        parser.error('A safe --run-id is required')
    if args.command == 'generate' and args.prompt_file is None:
        parser.error('generate requires --prompt-file')
    receipt = None
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get('downloaded') else 2
    except ResearchError as exc:
        receipt = receipt_for(args.state_dir, args.run_id)
        data = receipt.data or {}
        if receipt.data:
            receipt.update(reason=exc.code, error_kind=headless.classify_error(exc.code))
        print(json.dumps({'error': exc.code, 'message': str(exc), 'send_state': data.get('send_state'),
                          'send_clicks': data.get('send_clicks', 0), 'retry_safe': headless.retry_safe(data or None)}, indent=2))
        return 2


if __name__ == '__main__':
    sys.exit(main())
