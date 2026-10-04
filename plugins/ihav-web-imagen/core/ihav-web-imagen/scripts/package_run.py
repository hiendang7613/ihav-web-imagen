"""Fast package runner for the headless ChatGPT Images route: one process, one browser, one send per package.

Per package: fresh chat in Create image mode, references uploaded in order, exact prompt, Send once, condition-based
wait for that turn's finished image, then the original file saved from that turn's viewer. Library verification is
optional. Several packages run together with bounded concurrency, each on its own page with its own receipt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headless
from headless import Receipt, ResearchError, mark
from image_files import (MIMES, publish_original, reconcile_original, snapshot_references,
                         upload_references, validate_reference_paths)

STOP_CODES = {'login_required', 'human_verification_required', 'chatgpt_alert'}
DOWNLOAD = re.compile(r'^Download( file)?$')
PREVIEWS = '[data-testid="generated-image-gallery"] [data-testid="generated-image-preview"] img'


def load_package(directory):
    """(prompt, reference paths in prompt order) of a package folder: prompt.txt and package.json `order`."""
    directory = Path(directory).resolve(strict=True)
    prompt_file, meta = directory / 'prompt.txt', directory / 'package.json'
    if not prompt_file.is_file() or not meta.is_file():
        raise ResearchError('package_invalid', 'A package needs prompt.txt and package.json')
    prompt = prompt_file.read_text(encoding='utf-8').strip()
    if not prompt or len(prompt) > 16000:
        raise ResearchError('invalid_prompt', 'Prompt must contain 1 to 16000 characters')
    order = json.loads(meta.read_text(encoding='utf-8')).get('order')
    if not isinstance(order, list) or len(order) > 2:
        raise ResearchError('package_invalid', 'package.json needs `order`: at most two reference files, in prompt order')
    paths = []
    for item in order:
        name = str(item).split()[0] if str(item).split() else ''
        path = (directory / name).resolve()
        if path.parent != directory or not path.is_file() or path.suffix.lower() not in MIMES:
            raise ResearchError('package_invalid', f'Reference {name!r} is not a PNG/JPEG/WebP file of the package')
        paths.append(path)
    return prompt, paths


def run_id_for(directory, taken):
    stem = re.sub(r'[^a-zA-Z0-9_-]+', '-', Path(directory).resolve().name).strip('-')[:60] or 'package'
    base = f"{stem}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}"
    run_id, number = base, 1
    while run_id in taken:
        number += 1
        run_id = f'{base}-{number}'
    taken.add(run_id)
    return run_id


def create_runs(state, packages, download_dir, browser=None):
    """Validate every package first, then write each not_sent receipt; a bad package leaves no receipt behind."""
    prepared, taken = [], set()
    for package in packages:
        prompt, paths = load_package(package)
        paths = validate_reference_paths(paths)
        run_id = run_id_for(package, taken)
        if any(download_dir.glob(run_id + '.*')):
            raise ResearchError('download_target_exists', 'An existing output file was preserved before sending')
        prepared.append((package, prompt, paths, run_id))
    jobs = []
    for package, prompt, paths, run_id in prepared:
        directory = state / 'headless-images' / run_id
        receipt = None
        staging = None
        final_references = directory / 'references'
        references_moved = False
        create_attempted = False
        try:
            with headless.run_lock(directory):
                receipt = Receipt(directory / 'receipt.json')
                if receipt.path.exists() or receipt.data is not None:
                    raise ResearchError('run_exists_use_resume', 'This run already exists; observe it with resume')
                references = []
                if paths:
                    staging = Path(tempfile.mkdtemp(prefix='.references-', dir=directory))
                    staged_references = snapshot_references(staging, paths)
                    if final_references.exists():
                        raise ResearchError('reference_snapshot_exists', 'An existing reference snapshot was preserved')
                    os.replace(staging / 'references', final_references)
                    references_moved = True
                    references = [{**item, 'snapshot_path': str(final_references / item['filename'])}
                                   for item in staged_references]
                create_attempted = True
                receipt.create(run_id, prompt, reference_count=len(paths), references=references,
                               reference_state='ready' if references else 'not_requested',
                               download_stem=str(download_dir.resolve() / run_id),
                               package=str(Path(package).resolve()), browser=browser)
        except Exception as exc:
            # Nothing in this package is durable until snapshots and its complete receipt are ready.
            if references_moved and receipt is not None and not receipt.path.exists():
                try:
                    shutil.rmtree(final_references)
                except OSError:
                    pass                                                        # preserve the setup error
            partial = list(jobs)
            if create_attempted and receipt is not None and receipt.path.is_file():
                partial.append(receipt)
            try:
                exc.partial_receipts = partial
            except Exception:
                pass
            raise
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
        jobs.append(receipt)
    return jobs


async def save_original(page, control, receipt, expected, target_stem):
    """Click the viewer's download control once; publish the file only if its raster is the owned turn's image."""
    if not receipt.data.get('download_stem'):
        # Older runner receipts stored a stem under download_file. Normalize it
        # at the runner boundary; headless.py's explicit file paths stay exact.
        receipt.update(download_stem=str(target_stem), download_file=None)
    await publish_original(page, control, receipt, expected)


async def download_from_turn(page, receipt):
    """Open the owned turn's single generated image, check the viewer shows that raster, save its original file."""
    data = receipt.data
    if reconcile_original(receipt):
        return
    expected = {i['pixel_sha256'] for i in data.get('image_fingerprints', []) if i.get('pixel_sha256')}
    if len(expected) != 1 or len(data.get('image_dimensions', [])) != 1:
        raise ResearchError('download_identity_unverified', 'The owned turn lacks one rendered pixel identity')
    previews = page.locator(PREVIEWS)
    if await previews.count() != 1:
        raise ResearchError('download_identity_unverified', 'The owned turn does not show exactly one generated image')
    await previews.first.click()
    deadline = time.monotonic() + 20
    while True:
        views = await page.evaluate(headless.VIEWER_FINGERPRINTS, {'dimensions': data['image_dimensions'], 'cardName': None})
        if any(v.get('pixel_sha256') in expected for v in views):
            break
        if time.monotonic() >= deadline:
            raise ResearchError('download_identity_unverified', 'The opened viewer does not match the owned turn')
        await asyncio.sleep(0.25)
    control = page.get_by_role('button', name=DOWNLOAD)
    if await control.count() != 1:
        raise ResearchError('download_control_unverified', 'The viewer download control is ambiguous')
    await save_original(page, control, receipt, expected,
                        Path(data.get('download_stem') or data['download_file']))


async def flow(browser, receipt, seen, *, wait_seconds, verify_library, stop, effort=None):
    """Fresh chat -> references -> prompt -> one Send -> wait -> original file. `seen` keeps the page for recovery.

    `effort` ('instant' | 'medium') changes the profile-wide effort chip after the fresh composer is verified and restores it
    when this package is completely done (upload, Send, wait, download, optional library check), also on failure.
    """
    if stop.is_set():
        receipt.update(reason='batch_stopped_before_send')
        return
    started = time.monotonic()
    mark(receipt, 'start')
    page, editor, scope, baseline = await headless.prepare(browser, popups=False)
    seen['page'] = page
    mark(receipt, 'composer_ready')      # library page, New, empty composer checks
    async with headless.effort_scope(page, effort, receipt):
        if effort:
            mark(receipt, 'effort_ready')
        references = receipt.data.get('references', [])
        attachments = await upload_references(browser, page, references) if references else []
        receipt.update(attachments=attachments)
        mark(receipt, 'upload_ready')
        receipt.update(baseline_key_hashes=[headless.digest(i['key']) for i in baseline],
                       control_seconds=round(time.monotonic() - started, 3),
                       observation_deadline=(datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)).isoformat())
        if stop.is_set():
            receipt.update(reason='batch_stopped_before_send')
            return
        generation_started = time.monotonic()
        await headless.submit_once(page, editor, scope, receipt, browser=browser, stop=stop)
        if not await headless.observe(page, receipt, wait_seconds=wait_seconds, generation_started=generation_started):
            # An alert or an `unknown` send (turn not verified, ambiguous ownership) means the UI state is not understood:
            # packages still queued must not Send. Runs already sent keep going and keep their receipts.
            if receipt.data.get('reason') == 'visible_chatgpt_alert' or receipt.data.get('send_state') == 'unknown':
                stop.set()
            return
        await download_from_turn(page, receipt)
        mark(receipt, 'downloaded')
        if verify_library:
            await headless.verify_library(browser, page, receipt)
            if receipt.data.get('library_verified'):
                mark(receipt, 'library_verified')


async def run_one(browser, receipt, *, wait_seconds, verify_library, slots, stop, effort=None):
    """One package, never re-sent. Any failure is kept in its receipt; returns the receipt summary."""
    seen = {}
    try:
        with headless.run_lock(receipt.path.parent):
            async with slots:
                await flow(browser, receipt, seen, wait_seconds=wait_seconds, verify_library=verify_library, stop=stop,
                           effort=effort)
    except Exception as exc:
        code = exc.code if isinstance(exc, ResearchError) else type(exc).__name__
        if code == 'run_busy':
            # Output only: the owner's receipt is never rewritten, and its older error kind/retry verdict must not leak into this error.
            owned = Receipt(receipt.path)
            kind = headless.classify_error('run_busy')
            return {**owned.summary(), 'error': 'run_busy', 'reason': 'run_busy', 'error_kind': kind,
                    'retry_safe': headless.retry_safe(owned.data, kind)}
        fields = {'reason': code, 'error_kind': headless.classify_error(code)}
        if getattr(exc, 'diagnostic', None):
            fields['diagnostic'] = exc.diagnostic
        if receipt.data['send_state'] == 'intent':
            fields['send_state'] = 'unknown'
        page = seen.get('page')
        if page is not None and headless.conversation_url(page.url):
            fields['conversation_url'] = headless.conversation_url(page.url)
        receipt.update(**fields)
        # A chip that could not be restored leaves a changed account setting: the next package would record it as "previous".
        effort_left_changed = (receipt.data.get('effort') or {}).get('restore_state') == 'failed'
        if code in STOP_CODES or receipt.data['send_state'] == 'unknown' or effort_left_changed:
            stop.set()
    return receipt.summary()


async def run_all(args, receipts):
    started = time.monotonic()
    stop = asyncio.Event()
    slots = asyncio.Semaphore(args.concurrency)
    kind = headless.browser_for(args, receipts[0] if receipts else None)       # the runs' receipts decide; one kind per batch
    async with headless.session(args.state_dir, pause_idle_worker=args.pause_idle_worker, accept_downloads=True,
                                browser_kind=kind) as browser:
        # Profile lock, worker pause and browser start happen before any receipt stamp: keep them measurable.
        session_seconds = round(time.monotonic() - started, 3)
        for receipt in receipts:
            receipt.update(session_seconds=session_seconds)
        results = await asyncio.gather(*[
            run_one(browser, r, wait_seconds=args.wait_seconds, verify_library=args.verify_library, slots=slots, stop=stop,
                    effort=getattr(args, 'effort', None))
            for r in receipts])
    return {'runs': results, 'session_seconds': session_seconds, 'wall_seconds': round(time.monotonic() - started, 3),
            'concurrency': args.concurrency}


def batch_failure(code, receipts):
    """Output for a failure outside any single package (session start or teardown, a crash around the batch).

    The verdict comes from the receipts that exist, never from the error alone: `retry_safe` is true only when the error kind
    allows a retry AND no package of the batch was submitted. A receipt still at `intent` is unknown now (the same rule as in
    `run_one`); `runs` lists every package so a mixed batch shows which ones are retry-safe.
    """
    kind = headless.classify_error(code)
    summaries = []
    for receipt in receipts:
        if receipt.data['send_state'] == 'intent':
            receipt.update(send_state='unknown', reason=receipt.data.get('reason') or code,
                           error_kind=receipt.data.get('error_kind') or kind)
        summaries.append(receipt.summary())
    safe = headless.retry_safe(None, kind) and all(item['retry_safe'] for item in summaries)
    out = {'error': code, 'error_kind': kind, 'retry_safe': safe}
    if summaries:
        out['runs'] = summaries
    return out


def succeeded(summary, verify_library):
    return not summary.get('error') and bool(summary.get('downloaded')) and (not verify_library or summary.get('library_verified'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, action='append', required=True,
                        help='folder with prompt.txt and package.json (`order`: the reference files); repeat for a batch')
    parser.add_argument('--download-dir', type=Path, required=True, help='where the original images are saved; never overwritten')
    parser.add_argument('--state-dir', type=Path, default=headless.DEFAULT_STATE)
    parser.add_argument('--concurrency', type=int, default=None,
                        help='packages in flight at once (1-4); default 2, or 1 with --effort')
    parser.add_argument('--effort', choices=headless.EFFORT_CHOICES, type=str.lower,
                        help='set the ChatGPT effort chip (instant or medium) for each package, then restore it; '
                             'a profile-wide account setting, so packages run one at a time (not verified live yet)')
    parser.add_argument('--browser', choices=headless.BROWSER_KINDS, default=None,
                        help='cloakbrowser (default) or chrome (plain Google Chrome in its own profile, signed in once with headless.py login --browser chrome)')
    parser.add_argument('--verify-library', action='store_true', help='also confirm each image in Images (off by default)')
    parser.add_argument('--pause-idle-worker', action='store_true')
    parser.add_argument('--wait-seconds', type=float, default=600)
    args = parser.parse_args(argv)
    if args.concurrency is None:
        args.concurrency = 1 if args.effort else 2
    if not 1 <= args.concurrency <= 4:
        parser.error('--concurrency must be between 1 and 4')
    if args.effort and args.concurrency != 1:
        parser.error('--effort changes a profile-wide setting: use --concurrency 1')
    if not 0 < args.wait_seconds <= 1200:
        parser.error('--wait-seconds must be between 0 and 1200')
    receipts = []
    try:
        receipts = create_runs(args.state_dir, args.package, args.download_dir, browser=args.browser)
        result = asyncio.run(run_all(args, receipts))
    except Exception as exc:
        code = exc.code if isinstance(exc, ResearchError) else type(exc).__name__
        receipts = getattr(exc, 'partial_receipts', receipts)
        print(json.dumps(batch_failure(code, receipts), indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if all(succeeded(r, args.verify_library) for r in result['runs']) else 2


if __name__ == '__main__':
    sys.exit(main())
