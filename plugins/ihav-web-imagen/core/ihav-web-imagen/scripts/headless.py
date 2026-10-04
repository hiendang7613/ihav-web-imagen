"""Normal ChatGPT UI, existing authenticated headless profile, one send per run."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

# The vendored ChatGPT web adapter (chatgpt_web/, see its PROVENANCE.md) owns the browser lifecycle and the
# OS profile lock. No profile copying, cookie export, private HTTP calls, or automatic sign-in.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chatgpt_web.browser import required
from chatgpt_web.core import DEFAULT_STATE, ResearchError, atomic_write, json_bytes, load_config
from chatgpt_web.host import profile_lock
import sites
from image_files import (BROWSER_KINDS, Browser, ChromeBrowser, DEFAULT_BROWSER, download_owned, remove_probe_attachments,
                         snapshot_references, upload_references, validate_attachment_order,
                         validate_reference_paths)

LIBRARY = 'https://chatgpt.com/space/files?tab=images'
EDITOR = '#prompt-textarea,[contenteditable="true"][role="textbox"][aria-label="Ask ChatGPT"]'
SEND = 'button[aria-label="Send"],button[data-testid="send-button"],button[aria-label="Send prompt"],button[aria-label="Send message"]'
STOP = 'button[data-testid="stop-button"],button[aria-label="Stop streaming"],button[aria-label="Stop generating"],button[aria-label="Stop"],button[aria-label*="Stop"]'
USER = '[data-message-author-role="user"],[data-user-message-bubble]'
RUN_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}\Z')
POLL_SECONDS = 1.0  # observation cadence; a condition check, not a fixed wait
EFFORT_CHIP = 'button[aria-label="Select ChatGPT model"]'
EFFORT_LABELS = {'instant':'Instant', 'medium':'Medium'}
EFFORT_CHOICES = tuple(EFFORT_LABELS)

IMAGE_RECORDS = r'''es => es.filter(e=>e.getClientRects().length).map(e=>{
 const u=new URL(e.currentSrc||e.src,location.href);
 const fileId=u.searchParams.get('id')||u.searchParams.get('fileId');
 return {key:fileId||u.href,file_id:fileId,width:e.naturalWidth,height:e.naturalHeight,loaded:e.complete&&e.naturalWidth>0};
})'''

DOM_HELPERS = r'''
 const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
 const norm=s=>(s||'').replace(/\s+/g,' ').trim();
 const record=e=>{
  const u=new URL(e.currentSrc||e.src,location.href);
  const fileId=u.searchParams.get('id')||u.searchParams.get('fileId');
  return {key:fileId||u.href,file_id:fileId,width:e.naturalWidth,height:e.naturalHeight,loaded:e.complete&&e.naturalWidth>0};
 };
  // A long prompt is collapsed by the UI: the bubble text is the whole prompt, a separate visible control reading "…", and a
 // "Show more" button ("Show less" once expanded, with no span). Remove only those UI parts, never the prompt's own
 // characters: the trailing "…" goes only when that span exists, and only the toggle's own label is cut from the end.
 const bubbleText=e=>{
  const text=norm(e.innerText);
 const labels=[...e.querySelectorAll('button,[role="button"]')].map(b=>norm(b.innerText)).filter(t=>t==='Show more'||t==='Show less');
 if(labels.length!==1) return text;
 const uiEllipsis=[...e.querySelectorAll('span,button,[role="button"]')].some(s=>visible(s)&&norm(s.innerText||s.textContent)==='…');
  const tail=labels[0]==='Show less'?/\s*Show less$/:(uiEllipsis?/\s*…\s*Show more$/:/\s*Show more$/);
  return text.replace(tail,'').trim();
 };
 const owned=prompt=>{
 const roles=[...document.querySelectorAll('[data-message-author-role]')];
 const candidates=[...document.querySelectorAll('[data-message-author-role="user"],[data-user-message-bubble]')].filter(visible);
 const users=candidates.filter(e=>!candidates.some(other=>other!==e&&e.contains(other)));
 const matches=users.filter(e=>bubbleText(e)===norm(prompt));
 const match=matches.length===1?matches[0]:null;
 const legacy=match?.closest('[data-message-author-role="user"]');
 const after=legacy?roles.slice(roles.indexOf(legacy)+1):[];
 const nextUser=after.findIndex(e=>e.getAttribute('data-message-author-role')==='user');
 const tail=(nextUser<0?after:after.slice(0,nextUser)).filter(e=>e.getAttribute('data-message-author-role')==='assistant');
 const boundary=match?.closest('[data-turn-key],[data-turn-id],article,[data-testid^="conversation-turn-"]');
 const newGallery=boundary?.querySelector('[data-testid="generated-image-gallery"]');
 const imageNodes=newGallery?[...newGallery.querySelectorAll('[data-testid="generated-image-preview"] img')]:tail.flatMap(e=>[...e.querySelectorAll('img')]);
 return {users,matches,boundary,newGallery,tail,imageNodes:imageNodes.filter(visible)};
 };
 // Compare pixels already rendered by the normal UI. No fetch, asset export,
 // download, cookie read, or private application state is involved.
 const fingerprint=async e=>{
  if(!e.complete||e.naturalWidth<128||e.naturalHeight<128) return null;
  try {
   const canvas=document.createElement('canvas');canvas.width=32;canvas.height=32;
   const ctx=canvas.getContext('2d');ctx.drawImage(e,0,0,32,32);
   const hash=await crypto.subtle.digest('SHA-256',ctx.getImageData(0,0,32,32).data);
   return [...new Uint8Array(hash)].map(b=>b.toString(16).padStart(2,'0')).join('');
  } catch {return null;}
 };
'''

TURN_STATE = '({prompt,stop}) => {' + DOM_HELPERS + r'''
 const {users,matches,boundary,newGallery,tail,imageNodes}=owned(prompt);
 const images=imageNodes.map(record).filter(e=>e.width>=128&&e.height>=128);
 return {user_count:users.length,matches:matches.length,assistant_count:newGallery?1:tail.length,images,
  turn_id:boundary?.getAttribute('data-turn-key')||boundary?.getAttribute('data-turn-id')||boundary?.getAttribute('data-testid')||null,
  generating:[...document.querySelectorAll(stop)].some(visible),
  // ChatGPT first shows a progressive preview at full natural size; its "Preview" badge is a leaf element of the turn.
  preview:(boundary?[boundary]:tail).some(t=>[...t.querySelectorAll('*')].some(e=>visible(e)&&!e.children.length&&norm(e.textContent)==='Preview')),
  alerts:[...document.querySelectorAll('[role="alert"]')].filter(visible).map(e=>norm(e.innerText)).filter(Boolean).slice(0,2)};
}'''

TURN_FINGERPRINTS = 'async ({prompt}) => {' + DOM_HELPERS + r'''
 const {users,matches,imageNodes}=owned(prompt);
 if(users.length!==1||matches.length!==1) return [];
 return await Promise.all(imageNodes.filter(e=>e.naturalWidth>=128&&e.naturalHeight>=128).map(async e=>({...record(e),pixel_sha256:await fingerprint(e)})));
}'''

LIBRARY_CARDS = '() => {' + DOM_HELPERS + r'''
 return [...document.querySelectorAll('main button')].filter(visible).filter(e=>e.querySelectorAll('img').length===1).map(e=>({
  name:e.getAttribute('aria-label')||norm(e.innerText),...record(e.querySelector('img'))
 })).filter(e=>e.loaded&&e.width>=128&&e.height>=128);
}'''

VIEWER_FINGERPRINTS = 'async ({dimensions,cardName}) => {' + DOM_HELPERS + r'''
 const es=[...document.images].filter(visible).filter(e=>(!cardName||e.alt===cardName)&&e.getBoundingClientRect().width>250&&dimensions.some(d=>d[0]===e.naturalWidth&&d[1]===e.naturalHeight));
 return await Promise.all(es.map(async e=>({name:e.alt,...record(e),pixel_sha256:await fingerprint(e)})));
}'''


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def skill_fingerprint():
    """sha256 over the (name, sha256) pairs of this skill's scripts: binds a receipt to the code that produced it."""
    parts = [f'{path.name}:{hashlib.sha256(path.read_bytes()).hexdigest()}' for path in sorted(Path(__file__).resolve().parent.glob('*.py'))]
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()


def download_requested(data):
    return bool(data.get('download_file') or data.get('download_stem'))


def norm(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def mark(receipt, name):
    """Stamp a run phase once: UTC time and seconds since the run's first stamp."""
    phases = dict(receipt.data.get('phases', {}))
    if name in phases:
        return
    now = datetime.now(timezone.utc)
    phases[name] = now.isoformat()
    first = datetime.fromisoformat(next(iter(phases.values())))
    seconds = dict(receipt.data.get('phase_seconds', {}))
    seconds[name] = round((now - first).total_seconds(), 3)
    receipt.update(phases=phases, phase_seconds=seconds)


def conversation_url(value):
    parsed = urlparse(value)
    if parsed.scheme == 'https' and parsed.netloc == 'chatgpt.com' and re.fullmatch(r'/c/[a-zA-Z0-9_-]+', parsed.path):
        return 'https://chatgpt.com' + parsed.path
    return None


async def _effort_chip(page):
    chip = page.locator(EFFORT_CHIP)
    await required(chip, 'effort_control_unavailable', timeout=10000)
    if await chip.count() != 1 or not await chip.is_visible():
        raise ResearchError('effort_control_unavailable', 'The model/effort chip is not uniquely visible')
    value = norm(await chip.inner_text())
    if not value:
        raise ResearchError('effort_state_unavailable', 'The current model/effort value is empty')
    return chip, value


async def _visible_effort_options(page, value):
    controls = page.locator('button:not([aria-label="Select ChatGPT model"]),[role="menuitem"],[role="menuitemradio"],[role="option"]')
    matches = []
    for index in range(await controls.count()):
        option = controls.nth(index)
        if not await option.is_visible():
            continue
        labels = [norm(await option.get_attribute('aria-label')), norm(await option.inner_text())]
        if any(label == value or re.match(rf'^{re.escape(value)}(?:\s*[,·(].*)$', label) for label in labels if label):
            matches.append(option)
    return matches


async def _wait_effort_value(page, value):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        _, selected = await _effort_chip(page)
        if selected == value:
            return
        await asyncio.sleep(0.2)
    raise ResearchError('effort_selection_unverified', 'The chip did not confirm the selected value')


async def _select_effort_option(page, value):
    chip, _ = await _effort_chip(page)
    await chip.click()
    matches = await _visible_effort_options(page, value)
    if len(matches) != 1:
        raise ResearchError('effort_option_unverified', 'The requested model/effort option is not unique in the visible menu')
    await matches[0].click()
    await _wait_effort_value(page, value)


def _record_effort(receipt, **fields):
    if receipt is None:
        return
    state = dict(receipt.data.get('effort', {}))
    state.update(fields)
    receipt.update(effort=state)


@asynccontextmanager
async def effort_scope(page, requested, receipt=None):
    """Temporarily select an exact visible effort option and restore the prior chip value.

    Package-run callers wrap upload through observation and must serialize this
    profile-wide setting (concurrency=1). With no request, this is a no-op.
    """
    if requested is None:
        yield None
        return
    requested = requested.strip().casefold() if isinstance(requested, str) else ''
    requested_label = EFFORT_LABELS.get(requested)
    if requested_label is None:
        raise ResearchError('effort_unsupported', 'Supported effort values are instant and medium')

    _, previous = await _effort_chip(page)
    changed = previous != requested_label
    record = {'requested':requested, 'previous':previous, 'selected':previous,
              'observed_after_selection':previous,
              'state':'unchanged', 'changed':changed, 'captured_at':utc_now(),
              'apply_seconds':0.0, 'restore_seconds':0.0}
    if not changed:
        record['restore_state'] = 'not_required'
    def update_effort(**fields):
        record.update(fields)
        _record_effort(receipt, **fields)

    update_effort(**record)
    if changed:
        apply_started = time.monotonic()
        try:
            chip, _ = await _effort_chip(page)
            await chip.click()
            previous_options = await _visible_effort_options(page, previous)
            requested_options = await _visible_effort_options(page, requested_label)
            if len(previous_options) != 1:
                await page.keyboard.press('Escape')
                raise ResearchError('effort_restore_unverified', 'The previous chip value has no unique visible restore option')
            if len(requested_options) != 1:
                await page.keyboard.press('Escape')
                raise ResearchError('effort_option_unverified', f'The requested {requested_label} option is not unique in the visible menu')
            await requested_options[0].click()
            await _wait_effort_value(page, requested_label)
            _, observed = await _effort_chip(page)
            if observed != requested_label:
                raise ResearchError('effort_selection_unverified', 'The chip did not show the requested effort value')
        except Exception as exc:
            update_effort(state='selection_failed', apply_seconds=round(time.monotonic()-apply_started,3),
                          error=getattr(exc, 'code', type(exc).__name__))
            try:
                _, current = await _effort_chip(page)
                if current != previous:
                    restore_started = time.monotonic()
                    await _select_effort_option(page, previous)
                    update_effort(state='restored_after_selection_failure',
                                  restore_seconds=round(time.monotonic()-restore_started,3), restored_at=utc_now())
            except Exception as restore_exc:
                update_effort(restore_state='failed', restore_error=getattr(restore_exc, 'code', type(restore_exc).__name__))
            raise
        update_effort(selected=observed, observed_after_selection=observed,
                      state='applied', applied_at=utc_now(),
                      apply_seconds=round(time.monotonic()-apply_started,3))

    primary_error = None
    try:
        yield receipt.data.get('effort') if receipt is not None else record
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if changed:
            restore_started = time.monotonic()
            try:
                _, current = await _effort_chip(page)
                if current != previous:
                    await _select_effort_option(page, previous)
                _, observed_after_restore = await _effort_chip(page)
                if observed_after_restore != previous:
                    raise ResearchError('effort_restore_unverified', 'The original chip value was not restored')
                update_effort(state='restored', restored_at=utc_now(), restore_state='verified',
                              observed_after_restore=observed_after_restore,
                              restore_seconds=round(time.monotonic()-restore_started,3))
            except Exception as restore_exc:
                error_code = getattr(restore_exc, 'code', type(restore_exc).__name__)
                update_effort(state='restore_failed', restore_state='failed',
                              restore_error=error_code, restore_failed_at=utc_now(),
                              restore_seconds=round(time.monotonic()-restore_started,3))
                if primary_error is None:
                    raise ResearchError('effort_restore_failed', 'The original model/effort setting could not be restored') from restore_exc
                note = getattr(primary_error, 'diagnostic', {})
                if not isinstance(note, dict):
                    note = {}
                note['effort_restore'] = {'state':'failed', 'error':error_code}
                primary_error.diagnostic = note


# Machine-readable failure classes. `retry_safe` has ONE meaning: no image request was submitted, so trying again cannot
# create a duplicate. It is derived from the receipt (`send_state == not_sent` and no Send click), never from the error alone.
ERROR_KINDS = {
    'ambiguous_send': {'send_click_uncertain', 'resend_forbidden'},
    'site_state': {'login_required', 'human_verification_required', 'chatgpt_alert'},
    'busy': {'profile_busy', 'profile_owned_by_worker', 'research_active', 'already_generating', 'batch_stopped_before_send'},
    'run_state': {'run_exists_use_resume', 'run_missing', 'run_busy', 'receipt_invalid', 'recovery_url_missing',
                  'reference_preparation_incomplete'},
    'input': {'invalid_prompt', 'package_invalid', 'image_size_invalid', 'reference_file_invalid', 'reference_image_invalid',
              'reference_format_unsupported', 'reference_inventory_invalid', 'reference_snapshot_changed',
              'reference_snapshot_exists', 'download_target_exists', 'download_not_requested',
              'download_extension_mismatch', 'effort_unsupported'},
    'download': {'download_control_unverified', 'download_failed', 'download_format_unsupported',
                 'download_identity_unverified'},
    'environment': {'browser_binary_missing'},
}
UI_ERROR_PREFIXES = ('composer_', 'send_', 'upload_', 'attachment_', 'effort_', 'reference_', 'library_', 'creations_',
                     'owned_chat', 'probe_cleanup', 'image_decode', 'unexpected_', 'chat_mode', 'image_mode', 'not_fresh',
                     'temporary_chat', 'draft_mismatch', 'attachment')
NEVER_RETRY_KINDS = {'ambiguous_send', 'run_state'}   # the run already exists or its Send is unknown: resume/status, not a new request


def classify_error(code):
    """'ambiguous_send' | 'site_state' | 'busy' | 'run_state' | 'input' | 'download' | 'environment' | 'ui' | 'other'."""
    for kind, codes in ERROR_KINDS.items():
        if code in codes:
            return kind
    return 'ui' if isinstance(code, str) and code.startswith(UI_ERROR_PREFIXES) else 'other'


def retry_safe(data, error_kind=None):
    """True when nothing was submitted for this run. `data` is a receipt's data, or None when no receipt exists yet."""
    if error_kind in NEVER_RETRY_KINDS:
        return False
    if not data:
        return True
    return data.get('send_state') == 'not_sent' and not data.get('send_clicks')


class Receipt:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.is_file() else None
        if path.is_file() and (not isinstance(self.data, dict) or
                self.data.get('schema_version') != 1 or
                self.data.get('send_state') not in {'not_sent', 'intent', 'confirmed', 'unknown'} or
                not isinstance(self.data.get('prompt'), str) or
                self.data.get('prompt_sha256') != digest(self.data['prompt'])):
            raise ResearchError('receipt_invalid', 'Existing state does not prove that the request is unsent')

    def create(self, run_id, prompt, *, reference_count=0, references=None,
               reference_state=None, download_stem=None, package=None, browser=None):
        if self.path.exists() or self.data is not None:
            raise ResearchError('run_exists_use_resume', 'This run already exists; observe it with resume')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data = {'schema_version': 1, 'run_id': run_id, 'route': 'headless',
                     'created_at': utc_now(), 'prompt': prompt, 'prompt_sha256': digest(prompt),
                     'send_state': 'not_sent', 'generation_state': 'not_started',
                     'library_verified': False, 'downloaded': False, 'send_clicks': 0,
                     'skill_sha256': skill_fingerprint(), 'browser': browser or DEFAULT_BROWSER,
                     'required_reference_count':reference_count,
                     'reference_state':'preparing' if reference_count else 'not_requested'}
        if references is not None:
            self.data['references'] = references
        if reference_state is not None:
            self.data['reference_state'] = reference_state
        if download_stem is not None:
            self.data['download_stem'] = download_stem
        if package is not None:
            self.data['package'] = package
        self.update()

    def update(self, **fields):
        self.data.update(fields)
        self.data['updated_at'] = utc_now()
        atomic_write(self.path, json_bytes(self.data))

    def summary(self):
        keys = ('run_id', 'route', 'send_state', 'generation_state', 'library_verified',
                'downloaded', 'send_clicks', 'conversation_url', 'image_dimensions',
                'control_seconds', 'generation_seconds', 'observation_seconds',
                'recovered', 'library_verification_method', 'library_card_names',
                'library_verified_at', 'reason', 'preview_path', 'library_preview_path',
                'references', 'attachments', 'download_file', 'download_state',
                'original_file', 'requested_dimensions', 'phases', 'phase_seconds',
                'download_stem', 'required_reference_count', 'reference_state', 'diagnostic',
                'last_original_file', 'observation_last', 'skill_sha256', 'session_seconds', 'effort', 'error_kind', 'browser')
        return {**{k: self.data[k] for k in keys if k in self.data}, 'retry_safe': retry_safe(self.data, self.data.get('error_kind')),
                'receipt': str(self.path)}


@contextmanager
def run_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'run.lock').open('a+') as stream:
        os.chmod(stream.name, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResearchError('run_busy', 'Another process owns this image run') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def chrome_lock(state):
    """One process at a time in the Chrome profile."""
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'chrome-profile.lock').open('a+') as stream:
        os.chmod(stream.name, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResearchError('profile_busy', 'Another run or login window owns the Chrome profile') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def browser_for(args, receipt):
    """A run is observed in the profile it was created in (its receipt decides, an old receipt means CloakBrowser);
    only a new run or a probe chooses."""
    if receipt is not None and receipt.data:
        return receipt.data.get('browser') or DEFAULT_BROWSER
    return getattr(args, 'browser', None) or DEFAULT_BROWSER


@asynccontextmanager
async def chrome_session(state, *, accept_downloads=False):
    with chrome_lock(state):
        browser = ChromeBrowser(state, load_config(state))
        try:
            await browser.start(headless=True, accept_downloads=accept_downloads)
            yield browser
        finally:
            await browser.close()


async def login_chrome(state):
    """Sign in to ChatGPT once in the Chrome profile: a visible window, closed by the user. Sends nothing."""
    with chrome_lock(state):
        browser = ChromeBrowser(state, load_config(state))
        try:
            await browser.open_login_page()
            print(json.dumps({'login_window_open': True, 'browser': 'chrome',
                              'instruction': 'Sign in manually, then close the Chrome window. No prompt will be sent.'}), flush=True)
            await browser.wait_until_closed()
            return {'login_window_closed': True, 'browser': 'chrome', 'login_verified': False,
                    'next': 'a --dry-run does not check sign-in; the first real run stops before any Send if you are not signed in'}
        finally:
            await browser.close()


@asynccontextmanager
async def session(state, *, pause_idle_worker=False, accept_downloads=False, browser_kind=DEFAULT_BROWSER):
    """One headless browser in the chosen profile. `pause_idle_worker` is accepted for older callers and ignored:
    this runtime has no background worker that could own the profile."""
    if browser_kind == 'chrome':
        async with chrome_session(state, accept_downloads=accept_downloads) as browser:
            yield browser
        return
    with profile_lock(state):
        browser = Browser(state, load_config(state))
        try:
            await browser.start(headless=True, accept_downloads=accept_downloads)
            yield browser
        finally:
            await browser.close()


async def login_cloak(state, urls=('https://chatgpt.com/',)):
    """Sign in once in the CloakBrowser profile: one visible window, one tab per site, closed by the user. Sends nothing."""
    with profile_lock(state):
        browser = Browser(state, load_config(state))
        try:
            await browser.start(headless=False)
            for url in urls:
                page = await browser.context.new_page()
                with contextlib.suppress(Exception):          # a slow or blocked site must not stop the other tabs
                    await page.goto(url, wait_until='domcontentloaded')
            for blank in [p for p in browser.context.pages if p.url in ('about:blank', '')]:
                with contextlib.suppress(Exception):
                    await blank.close()
            print(json.dumps({'login_window_open': True, 'browser': 'cloakbrowser', 'tabs': list(urls),
                              'instruction': 'Sign in to each site you want to use, then close the browser window. No prompt will be sent.'}), flush=True)
            while browser.context.pages and any(not open_page.is_closed() for open_page in browser.context.pages):
                await asyncio.sleep(1)
            return {'login_window_closed': True, 'browser': 'cloakbrowser', 'tabs': list(urls), 'login_verified': False,
                    'next': 'a --dry-run does not check sign-in; the first real run stops before any Send if you are not signed in'}
        finally:
            await browser.close()


# The Images library's new-image control: "New" until 2026-10, "Create image" in the 2026-10-04 survey.
NEW_IMAGE = re.compile(r'^(?:New|Create image)$')


async def library_page(browser):
    page = await browser.context.new_page()
    await page.goto(LIBRARY, wait_until='domcontentloaded')
    await required(page.get_by_role('button', name=NEW_IMAGE), 'library_unavailable', timeout=45)
    await browser.check_access(page, strict_alerts=False)
    creations = await required(page.get_by_role('tab', name='Your creations', exact=True), 'creations_tab_unavailable')
    if await creations.get_attribute('aria-selected') == 'false':
        await creations.click()
    return page


async def composer_state(page):
    editor = await required(page.locator(EDITOR), 'composer_unavailable', timeout=60)
    scope = editor.locator('xpath=ancestor::form[1]')
    if not await scope.count():
        scope = editor.locator('xpath=ancestor::*[@data-composer-body][1]')
    if not await scope.count():
        raise ResearchError('composer_scope_unavailable', 'Cannot identify the current composer boundary')
    inventory = await scope.evaluate(r'''e=>({
      text:e.innerText,
      images:[...e.querySelectorAll('img')].filter(e=>e.getClientRects().length).map(e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height,alt:e.alt})),
      remove_labels:[...e.querySelectorAll('button')].map(e=>e.getAttribute('aria-label')||'').filter(e=>e!=='Remove Create image'&&/remove|attachment|uploading/i.test(e))
    })''')
    draft = await editor.inner_text()
    chat = await required(page.get_by_role('button', name='Chat', exact=True), 'chat_mode_unverified')
    if await chat.get_attribute('aria-pressed') != 'true':
        raise ResearchError('chat_mode_unverified', 'Chat mode is not selected')
    if not re.search(r'\bCreate image\b', inventory['text']):
        raise ResearchError('image_mode_unverified', 'The current composer does not show Create image')
    if any(i['width']>=48 and i['height']>=48 for i in inventory['images']) or inventory['remove_labels']:
        raise ResearchError('unexpected_attachment', 'Existing composer attachments were preserved')
    if await page.locator(USER).evaluate_all('es=>es.filter(e=>e.getClientRects().length).length'):
        raise ResearchError('not_fresh_chat', 'Existing chat turns were preserved')
    if await page.locator(STOP).count():
        raise ResearchError('already_generating', 'Existing generation was preserved')
    if urlparse(page.url).query and 'temporary' in urlparse(page.url).query.lower():
        raise ResearchError('temporary_chat', 'Library persistence requires a normal chat')
    return editor, scope, draft


async def prepare(browser, *, popups=True):
    phase = {'stage':'library_entry'}
    try:
        return await _prepare(browser, popups=popups, phase=phase)
    except Exception as exc:
        diagnostic = {'prepare':phase}
        if isinstance(exc, ResearchError):
            exc.diagnostic = diagnostic
            raise
        error = ResearchError('composer_prepare_failed', 'A browser action failed before the composer was ready')
        error.diagnostic = diagnostic
        raise error from exc


async def _prepare(browser, *, popups, phase):
    library = await library_page(browser)
    phase['stage'] = 'creation_baseline'
    baseline = await library.locator('main img').evaluate_all(IMAGE_RECORDS)
    before = set(browser.context.pages)
    phase['stage'] = 'new_image_chat'
    await library.get_by_role('button', name=NEW_IMAGE).click()
    # New has been observed to navigate this page; capture a popup as well when
    # a UI version creates one. Only task-owned pages are considered.
    deadline = time.monotonic() + 60
    phase['stage'] = 'composer_navigation'
    page = library
    while time.monotonic() < deadline:
        # Concurrent runs share the context: only a single-run caller may adopt a popup as its composer.
        owned = [library, *([p for p in browser.context.pages if p not in before] if popups else [])]
        for candidate in owned:
            if candidate.is_closed():
                continue
            if await candidate.locator(EDITOR).count():
                page = candidate
                break
        else:
            await asyncio.sleep(0.5)
            continue
        break
    phase['stage'] = 'composer_access'
    await browser.check_access(page, strict_alerts=False)
    phase['stage'] = 'image_tool'
    await select_image_tool(page)
    phase['stage'] = 'empty_image_composer'
    editor, scope, draft = await composer_state(page)
    if draft.strip():
        raise ResearchError('unexpected_draft', 'The existing draft was preserved')
    return page, editor, scope, baseline


IMAGE_TOOL_MENU = 'button[aria-label="Add files and more"]'


async def select_image_tool(page):
    """Since 2026-10-04 the library's Create image opens a plain composer; pick Create image from its + menu.

    Only a menu choice: nothing is typed or sent. When the composer already shows Create image (older UI), it does nothing.
    """
    editor = page.locator(EDITOR)
    if not await editor.count():
        return
    scope = editor.first.locator('xpath=ancestor::form[1]')
    if not await scope.count() or re.search(r'\bCreate image\b', await scope.first.inner_text()):
        return
    opener = page.locator(IMAGE_TOOL_MENU)
    if not await opener.count():
        return                                  # composer_state reports image_mode_unverified
    await opener.first.click()
    choice = page.get_by_text(re.compile(r'^Create image$'))
    deadline = time.monotonic() + 8
    while not await choice.count() and time.monotonic() < deadline:
        await asyncio.sleep(0.25)
    if await choice.count() != 1:
        await page.keyboard.press('Escape')
        return
    await choice.click()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not re.search(r'\bCreate image\b', await scope.first.inner_text()):
        await asyncio.sleep(0.25)


async def submit_once(page, editor, scope, receipt, *, browser=None, stop=None):
    if receipt.data['send_state'] != 'not_sent':
        raise ResearchError('resend_forbidden', 'An existing submission intent must be reconciled')
    prompt = receipt.data['prompt']
    await editor.fill(prompt)
    # Line breaks and indentation are re-flowed by the editor; the words must match exactly.
    if norm(await editor.inner_text()) != norm(prompt):
        raise ResearchError('draft_mismatch', 'The editor did not accept the exact prompt')
    mark(receipt, 'prompt_inserted')
    attachments = receipt.data.get('attachments', [])
    if browser is not None:
        await validate_attachment_order(browser, page, attachments)
    elif attachments:
        raise ResearchError('reference_inventory_unverified', 'Reference readiness must be checked before Send')
    send = await required(scope.locator(SEND), 'send_unavailable', hit=True)
    if not await send.is_enabled() or await send.get_attribute('aria-disabled') == 'true':
        raise ResearchError('send_not_ready', 'The application has not enabled Send')
    # A batch that has already met an unverified Send must not start another one: this is the last point before the
    # durable intent, so a package that was already past its earlier stop checks still stays `not_sent`.
    if stop is not None and stop.is_set():
        raise ResearchError('batch_stopped_before_send', 'An earlier package ended unverified; this package must not Send')
    # Durable intent precedes the only click. A click timeout is never replayed.
    receipt.update(send_state='intent', intent_at=utc_now(), before_send_url=page.url,
                   send_clicks=1)
    mark(receipt, 'intent')
    try:
        await send.click(timeout=10000)
    except Exception:
        fields = {'send_state':'unknown', 'reason':'send_click_uncertain'}
        if conversation_url(page.url):
            fields['conversation_url'] = conversation_url(page.url)
        receipt.update(**fields)
        raise ResearchError('send_click_uncertain', 'Reconcile this run; never submit it again')


async def observe(page, receipt, *, wait_seconds, generation_started=None):
    started = time.monotonic()
    saved_deadline = receipt.data.get('observation_deadline')
    remaining = (datetime.fromisoformat(saved_deadline)-datetime.now(timezone.utc)).total_seconds() if saved_deadline else wait_seconds
    deadline = started + min(wait_seconds, max(0, remaining))
    stable, settles = None, 0   # a finished image must read the same twice; the re-read is not cut short by the deadline
    while True:
        view = await page.evaluate(TURN_STATE, {'prompt': receipt.data['prompt'], 'stop': STOP})
        url = conversation_url(page.url)
        fields = {'conversation_url': url} if url else {}
        # What the last read saw: a run that ends unverified must say why (no prompt text, counts only).
        fields['observation_last'] = {'matches': view['matches'], 'user_count': view['user_count'],
                                      'images': len(view['images']), 'generating': view['generating'],
                                      'preview': bool(view.get('preview')), 'at': utc_now()}
        if view['matches'] == 1 and view['user_count'] == 1:
            fields.update(send_state='confirmed', turn_id=view['turn_id'], reason=None)
            if not receipt.data.get('confirmed_at'):
                fields['confirmed_at'] = utc_now()
                mark(receipt, 'turn_posted')
                print(json.dumps({'event': 'submission_confirmed', **{k: v for k, v in fields.items() if k != 'observation_last'}}), flush=True)
            complete = view['images'] and not view['generating'] and not view.get('preview') and all(i['loaded'] for i in view['images'])
            if view['images'] and not complete:
                mark(receipt, 'preview_visible')
            signature = json.dumps([[i['key'], i['width'], i['height']] for i in view['images']]) if complete else None
            if complete and signature == stable:
                mark(receipt, 'final_complete')
                images = await page.evaluate(TURN_FINGERPRINTS, {'prompt': receipt.data['prompt']})
                fields.update(generation_state='completed', images=view['images'],
                              image_dimensions=[[i['width'], i['height']] for i in view['images']],
                              image_fingerprints=images,
                              observation_seconds=round(time.monotonic()-started, 3),
                              recovered=generation_started is None)
                if generation_started is not None:
                    fields.update(generation_seconds=round(time.monotonic()-generation_started, 3),
                                  generation_timing_source='uninterrupted_observation')
                elif receipt.data.get('generation_timing_source') != 'uninterrupted_observation':
                    fields['generation_seconds'] = None
                receipt.update(**fields)
                return True
            if complete and settles < 3:
                stable, settles = signature, settles + 1
                fields['generation_state'] = 'pending'
                receipt.update(**fields)
                await asyncio.sleep(POLL_SECONDS)
                continue
            stable = signature
            fields['generation_state'] = 'pending'
        elif view['matches'] > 1 or view['user_count'] > 1:
            receipt.update(**fields, send_state='unknown', reason='turn_ownership_ambiguous')
            return False
        if view['alerts']:
            fields['reason'] = 'visible_chatgpt_alert'
            if receipt.data['send_state'] == 'intent' and 'send_state' not in fields:
                fields['send_state'] = 'unknown'
            receipt.update(**fields)
            return False
        receipt.update(**fields)
        if time.monotonic() >= deadline:
            if receipt.data['send_state'] == 'intent':
                receipt.update(send_state='unknown', reason='posted_prompt_unverified')
            return False
        await asyncio.sleep(min(POLL_SECONDS, max(0.1, deadline-time.monotonic())))


async def verify_library(browser, page, receipt):
    targets = receipt.data.get('image_fingerprints', [])
    file_ids = {i['file_id'] for i in targets if i.get('file_id')}
    fingerprints = {i['pixel_sha256'] for i in targets if i.get('pixel_sha256')}
    baseline = set(receipt.data.get('baseline_key_hashes', []))
    library = await library_page(browser)
    deadline = time.monotonic() + 45
    cards = []
    while True:
        cards = await library.evaluate(LIBRARY_CARDS)
        matches = [i for i in cards if i.get('file_id') in file_ids]
        if file_ids and len(file_ids) == len(targets) and file_ids <= {i['file_id'] for i in matches}:
            receipt.update(library_verified=True, library_url=LIBRARY,
                           library_verification_method='shared_rendered_file_id',
                           library_card_names=[i['name'] for i in matches],
                           library_verified_at=utc_now(), reason=None)
            return
        new_cards = [i for i in cards if digest(i['key']) not in baseline]
        if new_cards and fingerprints and len(fingerprints) == len(targets):
            break
        if time.monotonic() >= deadline:
            receipt.update(reason='library_identity_unverified')
            return
        await asyncio.sleep(3)
    # Open only newly observed cards. If concurrent activity creates too many
    # candidates, preserve the ambiguity instead of inspecting unrelated files.
    if len(new_cards) > 8:
        receipt.update(reason='library_candidates_ambiguous')
        return
    matched = set()
    names = []
    for card in new_cards:
        button = library.get_by_role('button', name=card['name'], exact=True).filter(has=library.locator('img'))
        if await button.count() != 1:
            continue
        await button.click()
        viewer_deadline = min(deadline, time.monotonic() + 20)
        while True:
            views = await library.evaluate(VIEWER_FINGERPRINTS, {
                'dimensions': receipt.data['image_dimensions'], 'cardName':card['name']})
            found = {i['pixel_sha256'] for i in views if i.get('pixel_sha256') in fingerprints}
            if found:
                matched.update(found)
                names.append(card['name'])
                target = receipt.path.parent / 'library-preview.png'
                await library.screenshot(path=str(target))
                target.chmod(0o600)
                receipt.update(library_preview_path=str(target))
                break
            if time.monotonic() >= viewer_deadline:
                break
            await asyncio.sleep(1.5)
        if fingerprints <= matched:
            receipt.update(library_verified=True, library_url=LIBRARY,
                           library_verification_method='rendered_pixel_match',
                           library_card_names=names, library_verified_at=utc_now(), reason=None)
            return
        # Re-enter the normal library; never edit, delete, or download a card.
        await library.goto(LIBRARY, wait_until='domcontentloaded')
        await required(library.get_by_role('button', name=NEW_IMAGE), 'library_unavailable')
        creations = await required(library.get_by_role('tab', name='Your creations', exact=True), 'creations_tab_unavailable')
        if await creations.get_attribute('aria-selected') == 'false':
            await creations.click()
    receipt.update(reason='library_identity_unverified')


async def finish_run(browser, page, receipt, args, generation_started):
    try:
        completed = await observe(page, receipt, wait_seconds=args.wait_seconds,
                                  generation_started=generation_started)
    except Exception:
        if conversation_url(page.url):
            receipt.update(conversation_url=conversation_url(page.url))
        raise
    if completed:
        await verify_library(browser, page, receipt)
        if download_requested(receipt.data) and receipt.data.get('library_verified'):
            download_page = await library_page(browser)
            await download_owned(download_page, receipt, VIEWER_FINGERPRINTS)
    if args.preview:
        # UI capture for verification, never the downloadable original asset.
        target = receipt.path.parent / 'preview.png'
        await page.screenshot(path=str(target))
        target.chmod(0o600)
        receipt.update(preview_path=str(target))
    return receipt.summary()


async def execute(args, receipt):
    started = time.monotonic()
    if args.command == 'probe':
        validate_reference_paths(getattr(args, 'reference', []) or [])
    if receipt and receipt.data['send_state'] == 'not_sent':
        refs = receipt.data.get('references', [])
        if receipt.data.get('reference_state') == 'preparing' or receipt.data.get('required_reference_count',len(refs)) != len(refs):
            raise ResearchError('reference_preparation_incomplete', 'Required references are not retained; do not send a text-only replacement')
    if args.command == 'resume':
        fields = {'error_kind':None, 'library_verified':False, 'library_verification_method':None,
                  'library_card_names':[], 'library_verified_at':None,
                  'library_preview_path':None, 'library_observation_started_at':utc_now()}
        if download_requested(receipt.data):
            fields.update(downloaded=False, download_state='verification_pending')
        if receipt.data.get('library_verified'):
            fields['last_library_verification'] = {
                'method':receipt.data.get('library_verification_method'),
                'at':receipt.data.get('library_verified_at'),
                'card_names':receipt.data.get('library_card_names', []),
                'preview_path':receipt.data.get('library_preview_path')}
        receipt.update(**fields)
    async with session(args.state_dir, pause_idle_worker=args.pause_idle_worker, browser_kind=browser_for(args, receipt),
                       accept_downloads=bool(receipt and download_requested(receipt.data))) as browser:
        if args.command == 'probe':
            # Seconds since this command began: profile lock/worker pause/browser start, then each UI step. No Send.
            timings = {'browser_started': round(time.monotonic()-started,3)}
            page, _, _, baseline = await prepare(browser)
            timings['composer_ready'] = round(time.monotonic()-started,3)
            with tempfile.TemporaryDirectory(prefix='chatgpt-reference-probe-') as directory:
                references = snapshot_references(Path(directory), getattr(args, 'reference', []) or [])
                attachments = await upload_references(browser, page, references) if references else []
                timings['upload_ready'] = round(time.monotonic()-started,3)
                if attachments:
                    await remove_probe_attachments(browser, page, attachments)
                timings['cleanup_done'] = round(time.monotonic()-started,3)
                return {'route': 'headless', 'headless': True, 'authenticated_library': True,
                        'image_composer_ready': True, 'send_state': 'not_sent',
                        'references':references, 'attachments':attachments,
                        'probe_attachments_removed':True, 'timings': timings, 'skill_sha256': skill_fingerprint(),
                        'baseline_image_count': len(baseline), 'elapsed_seconds': round(time.monotonic()-started,3)}
        generation_started = None
        if args.command == 'generate' or receipt.data['send_state'] == 'not_sent':
            page, editor, scope, baseline = await prepare(browser)
            async with effort_scope(page, getattr(args, 'effort', None), receipt):
                references = receipt.data.get('references', [])
                attachments = await upload_references(browser, page, references) if references else []
                receipt.update(references=references, attachments=attachments)
                receipt.update(baseline_key_hashes=[digest(i['key']) for i in baseline],
                               control_seconds=round(time.monotonic()-started,3),
                               observation_deadline=(datetime.now(timezone.utc)+timedelta(seconds=args.wait_seconds)).isoformat())
                generation_started = time.monotonic()
                await submit_once(page, editor, scope, receipt, browser=browser)
                await finish_run(browser, page, receipt, args, generation_started)
            return receipt.summary()
        else:
            url = receipt.data.get('conversation_url')
            if conversation_url(url or '') is None:
                raise ResearchError('recovery_url_missing', 'No retained conversation identity; do not regenerate')
            page = await browser.context.new_page()
            await page.goto(url, wait_until='domcontentloaded')
            await browser.check_access(page, strict_alerts=False)
            await required(page.locator(USER).first, 'owned_chat_not_loaded', timeout=45)
            receipt.update(resumed_at=utc_now(), observation_deadline=(
                datetime.now(timezone.utc)+timedelta(seconds=args.wait_seconds)).isoformat())
        return await finish_run(browser, page, receipt, args, generation_started)


def run_command(args):
    receipt = None
    try:
        if args.command != 'probe':
            receipt = Receipt(args.state_dir / 'headless-images' / args.run_id / 'receipt.json')
            if args.command == 'generate':
                if receipt.path.exists():
                    raise ResearchError('run_exists_use_resume', 'This run already exists; never send it again')
                prompt = args.prompt_file.read_text().strip()
                if not prompt or len(prompt) > 16000:
                    raise ResearchError('invalid_prompt', 'Prompt must contain 1 to 16000 characters')
                download = getattr(args, 'download_file', None)
                if download is not None and download.exists():
                    raise ResearchError('download_target_exists', 'An existing output file was preserved before sending')
                selected_references = getattr(args, 'reference', []) or []
                selected_references = validate_reference_paths(selected_references)
                receipt.create(args.run_id, prompt, reference_count=len(selected_references), browser=getattr(args, 'browser', None))
                references = snapshot_references(receipt.path.parent, selected_references)
                receipt.update(references=references,
                               reference_state='ready' if references else 'not_requested',
                               download_file=str(download.resolve()) if download else None,
                               requested_dimensions=getattr(args, 'requested_size', None))
            elif receipt.data is None:
                raise ResearchError('run_missing', 'No existing run was found')
            if args.command == 'status':
                print(json.dumps(receipt.summary(), indent=2))
                return 0
            if args.command == 'resume' and getattr(args, 'download_file', None):
                target = str(args.download_file.resolve())
                if target != receipt.data.get('download_file'):
                    fields = {'download_file':target, 'download_stem':None,
                              'downloaded':False, 'download_state':'requested', 'original_file':{}}
                    if receipt.data.get('original_file'):
                        fields['last_original_file'] = receipt.data['original_file']
                    receipt.update(**fields)
        result = asyncio.run(execute(args, receipt))
        print(json.dumps(result, indent=2))
        success = result.get('library_verified') and (not download_requested(result) or result.get('downloaded'))
        return 0 if args.command == 'probe' or success else 2
    except Exception as exc:
        code = exc.code if isinstance(exc, ResearchError) else type(exc).__name__
        diagnostic = getattr(exc, 'diagnostic', None)
        # A duplicate command is read-only: it must not replace an earlier result.
        kind = classify_error(code)
        if receipt is not None and receipt.data is not None and code != 'run_exists_use_resume':
            fields = {'reason': code, 'error_kind': kind}
            if diagnostic:
                fields['diagnostic'] = diagnostic
            if receipt.data['send_state'] == 'intent':
                fields['send_state'] = 'unknown'
            receipt.update(**fields)
        # With a receipt the summary already carries the diagnostic; print it separately only without one (probe).
        shown = receipt.summary() if receipt and receipt.data else ({'diagnostic':diagnostic} if diagnostic else {})
        # The kind/retry verdict of THIS error wins over what an older failure left in the receipt (duplicate `generate`).
        print(json.dumps({'error': code, **shown, 'error_kind': kind,
                          'retry_safe': retry_safe(receipt.data if receipt and receipt.data else None, kind)}, indent=2))
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('probe', 'generate', 'resume', 'status', 'login'))
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--run-id')
    parser.add_argument('--prompt-file', type=Path)
    parser.add_argument('--reference', type=Path, action='append', default=[], help='PNG/JPEG/WebP image; repeat in prompt order, at most two')
    parser.add_argument('--download-file', type=Path, help='Explicit destination for the original image; its extension must be .png, .jpg/.jpeg or .webp and match the native format; never overwrite')
    parser.add_argument('--effort', choices=EFFORT_CHOICES, type=str.lower,
                        help='Temporarily select instant or medium effort for this generation, then restore the prior setting')
    parser.add_argument('--requested-size', type=int, nargs=2, metavar=('WIDTH','HEIGHT'), help='Record requested dimensions; does not promise web pixel control')
    parser.add_argument('--browser', choices=BROWSER_KINDS, default=None,
                        help='cloakbrowser (default, its own profile) or chrome (plain Google Chrome in its own profile; sign in once with `login --browser chrome`). '
                             'Only a new run, a probe or login chooses: resume and status use the browser the run was created with')
    parser.add_argument('--pause-idle-worker', action='store_true', help=argparse.SUPPRESS)      # accepted for older commands; no effect
    parser.add_argument('--preview', action='store_true', help='Capture the UI for visual verification; do not download the original')
    parser.add_argument('--wait-seconds', type=float, default=600)
    parser.add_argument('--site', action='append', default=None, help='login only (CloakBrowser): a site id from sites.py or all (default: chatgpt)')
    args = parser.parse_args()
    if not 0 <= args.wait_seconds <= 1200:
        parser.error('--wait-seconds must be between 0 and 1200')
    if len(args.reference) > 2:
        parser.error('At most two references are supported')
    if args.reference and args.command not in {'probe','generate'}:
        parser.error('References are immutable after a run is created; resume its retained inputs')
    if args.requested_size and (args.command != 'generate' or min(args.requested_size) < 1):
        parser.error('--requested-size requires generate and two positive dimensions')
    if args.download_file and args.command not in {'generate','resume'}:
        parser.error('--download-file is supported only for generate/resume')
    if args.effort and args.command != 'generate':
        parser.error('--effort is supported only for a new generate run')
    if args.browser and args.command not in {'probe', 'generate', 'login'}:
        parser.error('--browser is supported only for probe, generate and login: a run is resumed in the browser it was created with')
    if args.command == 'login':
        if args.site and args.browser == 'chrome':
            parser.error('--site is supported only for the CloakBrowser profile')
        try:
            chosen = sites.urls(sites.resolve(args.site or ['chatgpt']))
        except ValueError as exc:
            parser.error(str(exc))
        try:
            if args.browser == 'chrome':
                result = asyncio.run(login_chrome(args.state_dir))
            else:
                result = asyncio.run(login_cloak(args.state_dir, chosen))
            print(json.dumps(result, indent=2))
            return 0
        except ResearchError as exc:
            print(json.dumps({'error': exc.code, 'error_kind': classify_error(exc.code)}))
            return 2
    if args.command == 'probe':
        return run_command(args)
    if not args.run_id or not RUN_ID.fullmatch(args.run_id):
        parser.error('A safe --run-id is required')
    if args.command == 'generate' and args.prompt_file is None:
        parser.error('generate requires --prompt-file')
    if args.command == 'status':
        return run_command(args)
    try:
        with run_lock(args.state_dir / 'headless-images' / args.run_id):
            return run_command(args)
    except ResearchError as exc:
        print(json.dumps({'error':exc.code, 'error_kind':classify_error(exc.code), 'retry_safe':retry_safe(None, classify_error(exc.code))}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
