"""Look at a signed-in web chat and record what its page offers, so an adapter can be written from evidence. Sends nothing.

    survey.py --site gemini            # one site
    survey.py --site all               # every site in sites.py, one after another
    survey.py --site all --diff        # then compare each site with its previous survey (what changed around the composer)
    survey.py --site gemini --diff-only   # compare the two newest surveys; no browser

For each site it opens the chat page headless in the CloakBrowser profile, waits for the page to settle, and writes
`<state>/survey/<site>/<timestamp>/survey.json` plus a screenshot: the final URL, sign-in and verification signs, composer
candidates, the visible buttons around the composer and on the page (aria-label, data-testid, text), file inputs and menus.
It never types, clicks, dismisses a banner or uploads. The files can show the account name or avatar: they stay in the
state folder, outside the repository, unless you choose to copy a redacted part.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sites  # noqa: E402
from chatgpt_web.core import DEFAULT_STATE, ResearchError, atomic_write, json_bytes, load_config  # noqa: E402
from chatgpt_web.host import profile_lock  # noqa: E402
from chatgpt_web.browser import Browser  # noqa: E402

SETTLE_SECONDS = 6.0

# Read-only DOM inventory: no event is dispatched, nothing is focused or changed.
INVENTORY = r'''() => {
  const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none');
  const text = e => (e.innerText || e.value || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  const describe = e => ({
    tag: e.tagName.toLowerCase(), id: e.id || null, role: e.getAttribute('role'), aria_label: e.getAttribute('aria-label'),
    testid: e.getAttribute('data-testid'), name: e.getAttribute('name'), type: e.getAttribute('type'),
    placeholder: e.getAttribute('placeholder') || e.getAttribute('data-placeholder'), disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true',
    text: text(e), classes: (e.className && typeof e.className === 'string') ? e.className.split(/\s+/).slice(0, 6).join(' ') : null,
  });
  const composers = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"], input[type="text"]')].filter(visible);
  const scopeOf = e => e.closest('form') || e.closest('[class*="composer" i], [class*="input" i], [class*="prompt" i]') || e.parentElement?.parentElement?.parentElement;
  const around = composers.slice(0, 3).map(c => {
    const scope = scopeOf(c);
    return {composer: describe(c), scope: scope ? describe(scope) : null,
            buttons: scope ? [...scope.querySelectorAll('button, [role="button"]')].filter(visible).slice(0, 60).map(describe) : [],
            file_inputs: scope ? [...scope.querySelectorAll('input[type="file"]')].map(describe) : []};
  });
  const words = /\b(log ?in|sign ?in|sign ?up|continue with|verify you are human|captcha|cloudflare)\b/i;
  return {
    url: location.href, title: document.title,
    signin_signs: [...document.querySelectorAll('a, button')].filter(visible).map(text).filter(t => words.test(t)).slice(0, 20),
    password_inputs: document.querySelectorAll('input[type="password"]').length,
    challenge_frames: [...document.querySelectorAll('iframe')].map(f => f.src).filter(s => /challenge|turnstile|captcha|recaptcha/i.test(s)).slice(0, 5),
    composers: around,
    page_buttons: [...document.querySelectorAll('button, [role="button"]')].filter(visible).slice(0, 120).map(describe),
    file_inputs: [...document.querySelectorAll('input[type="file"]')].map(describe),
    menus: [...document.querySelectorAll('[role="menu"], [role="listbox"], [aria-haspopup]')].filter(visible).slice(0, 30).map(describe),
    images: [...document.querySelectorAll('main img, [role="main"] img')].filter(visible).length,
  };
}'''


async def survey_one(browser, site: str, out_root: Path) -> dict:
    name, site_urls = sites.SITES[site]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = out_root / site / stamp
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    page = await browser.context.new_page()
    try:
        record = {'site': site, 'name': name, 'requested_url': site_urls[0], 'surveyed_at': stamp, 'sent': False}
        try:
            await page.goto(site_urls[0], wait_until='domcontentloaded', timeout=45000)
        except Exception as exc:
            record['navigation_error'] = f'{type(exc).__name__}: {str(exc)[:200]}'
        await asyncio.sleep(SETTLE_SECONDS)
        with contextlib.suppress(Exception):
            record['inventory'] = await page.evaluate(INVENTORY)
        with contextlib.suppress(Exception):
            await page.screenshot(path=str(out / 'page.png'))
            record['screenshot'] = str(out / 'page.png')
        inventory = record.get('inventory') or {}
        record['summary'] = {
            'final_url': inventory.get('url'),
            'looks_signed_out': bool(inventory.get('password_inputs') or inventory.get('signin_signs')),
            'challenge': bool(inventory.get('challenge_frames')),
            'composer_found': bool(inventory.get('composers')),
            'file_input_found': bool(inventory.get('file_inputs')),
        }
        atomic_write(out / 'survey.json', json_bytes(record))
        return {'site': site, 'file': str(out / 'survey.json'), **record['summary']}
    finally:
        with contextlib.suppress(Exception):
            await page.close()


def signature(record: dict) -> dict:
    """What an adapter depends on: the composer, the controls beside it, the file inputs and the sign-in state."""
    inventory = record.get('inventory') or {}
    first = (inventory.get('composers') or [{}])[0]
    control = lambda b: '|'.join(str(b.get(k) or '') for k in ('tag', 'aria_label', 'testid', 'text'))
    composer = first.get('composer') or {}
    return {
        'final_url_path': (inventory.get('url') or '').split('?')[0],
        'composer': control(composer) if composer else None,
        'composer_controls': sorted({control(b) for b in first.get('buttons') or []}),
        'file_inputs': len(first.get('file_inputs') or []) + len(inventory.get('file_inputs') or []),
        'looks_signed_out': (record.get('summary') or {}).get('looks_signed_out'),
        'challenge': (record.get('summary') or {}).get('challenge'),
    }


def diff(out_root: Path, site: str) -> dict:
    """Compare the two newest surveys of a site. An empty `changes` means nothing an adapter relies on moved."""
    runs = sorted((out_root / site).glob('*/survey.json'))
    if len(runs) < 2:
        return {'site': site, 'compared': False, 'reason': f'{len(runs)} survey(s) on disk; need two'}
    old, new = (signature(json.loads(path.read_text())) for path in runs[-2:])
    changes = {}
    for key in new:
        if key == 'composer_controls':
            added, removed = sorted(set(new[key]) - set(old[key])), sorted(set(old[key]) - set(new[key]))
            if added or removed:
                changes[key] = {'added': added, 'removed': removed}
        elif new[key] != old[key]:
            changes[key] = {'before': old[key], 'after': new[key]}
    return {'site': site, 'compared': True, 'before': runs[-2].parent.name, 'after': runs[-1].parent.name, 'changes': changes}


async def survey(state: Path, names: list[str], out_root: Path) -> list[dict]:
    with profile_lock(state):
        browser = Browser(state, load_config(state))
        await browser.start(headless=True)
        try:
            results = []
            for site in names:
                results.append(await survey_one(browser, site, out_root))
                print(json.dumps(results[-1]), flush=True)
            return results
        finally:
            await browser.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--site', action='append', required=True, help='a site id from sites.py, or all; repeatable')
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--out', type=Path, default=None, help='default: <state>/survey')
    parser.add_argument('--diff', action='store_true', help='after surveying, compare each site with its previous survey')
    parser.add_argument('--diff-only', action='store_true', help='only compare the two newest surveys on disk; no browser')
    args = parser.parse_args(argv)
    try:
        names = sites.resolve(args.site)
    except ValueError as exc:
        parser.error(str(exc))
    out_root = args.out or args.state_dir / 'survey'
    if not args.diff_only:
        try:
            asyncio.run(survey(args.state_dir, names, out_root))
        except ResearchError as exc:
            print(json.dumps({'error': exc.code, 'message': str(exc), 'sent': False}))
            return 2
    if args.diff or args.diff_only:
        reports = [diff(out_root, site) for site in names]
        for report in reports:
            print(json.dumps(report))
        return 3 if any(report.get('changes') for report in reports) else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
