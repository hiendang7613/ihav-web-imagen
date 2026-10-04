"""One-line front door: `imagine.py "a beautiful girl"` -> one image, saved to a file.

    imagine.py "a beautiful girl"
    imagine.py "same pose, watercolor" --ref photo.png --ref palette.png --out ~/Pictures/art   # --out: any folder you choose
    imagine.py "a red fox" --dry-run          # validates the package and the output folder; no browser, no Send (needs `runtime.py setup`)
    imagine.py "a red fox" --browser chrome   # plain Google Chrome in its own profile instead of the default CloakBrowser
    imagine.py - <<'TEXT'                      # the description from stdin: a quoted heredoc keeps $, backticks and quotes exactly as written
    a red fox, "golden hour", $50 poster
    TEXT

It builds a one-off package from the words, runs `package_run.py` once (headless authenticated profile, the normal ChatGPT
Images UI, exactly one Send, original file downloaded from the owned turn) and prints where the file is. The prompt is used
exactly as written. The script never retries: a second run is a second request, so a failure prints the run id and the
read-only recovery command (`headless.py resume` / `status`) instead.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

SKILL_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_SCRIPTS))
from chatgpt_web.paths import default_state, venv_python  # noqa: E402  (standard library only)

VENV_PYTHON = venv_python(default_state())
MAX_PROMPT = 16000
MAX_REFERENCES = 2
BROWSER_KINDS = ('cloakbrowser', 'chrome')                      # the same list as image_files.BROWSER_KINDS (a test pins it); that module needs the runtime to import
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}


def reexec_in_runtime():
    """The browser runtime lives in the private venv `runtime.py setup` made; hand over to it once (no install here)."""
    try:
        import cloakbrowser  # noqa: F401
        return
    except ImportError:
        pass
    # The venv's python is a symlink to the base interpreter, so compare prefixes (not resolved executables); the environment
    # flag stops a loop if the venv itself lacks the runtime.
    if VENV_PYTHON.is_file() and not os.environ.get('IMAGINE_REEXEC'):
        os.environ['IMAGINE_REEXEC'] = '1'
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(f'The browser runtime is not set up yet. Run once: {shlex.quote(sys.executable)} {shlex.quote(str(SKILL_SCRIPTS / "runtime.py"))} setup '
             '(then `runtime.py login` to sign in). Nothing was sent.')


def check_folder(path: Path):
    """Fail BEFORE any Send when the output folder cannot be written: the file is saved only after the paid request."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if not probe.is_dir() or not os.access(probe, os.W_OK | os.X_OK):
        raise SystemExit(f'Cannot save into {path}: {probe} is not a writable folder. Nothing was sent.')


def check_browser(kind: str):
    """Fail BEFORE any Send when the chosen browser is not installed (sign-in cannot be checked without starting it)."""
    if kind == 'chrome':
        import image_files
        if image_files.chrome_binary() is None:
            raise SystemExit('Google Chrome was not found in /Applications (set IHAV_WEB_IMAGEN_CHROME to its executable). Nothing was sent.')


def slug(text: str) -> str:
    """A short, safe file stem from the prompt words."""
    words = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:40].strip('-')
    return words or 'image'


def build_package(prompt: str, references: list[str], directory: Path, name: str | None = None) -> Path:
    """A package folder for package_run: prompt.txt, package.json `order`, and the references copied under distinct ordered names."""
    prompt = prompt.strip()
    if not prompt or len(prompt) > MAX_PROMPT:
        raise SystemExit(f'The prompt needs 1 to {MAX_PROMPT} characters (got {len(prompt)}). Nothing was sent.')
    if len(references) > MAX_REFERENCES:
        raise SystemExit(f'At most {MAX_REFERENCES} reference images, in prompt order (got {len(references)}). Nothing was sent.')
    folder = directory / (slug(name or prompt))
    folder.mkdir(parents=True)
    order = []
    for number, reference in enumerate(references, 1):
        source = Path(reference).expanduser()
        if not source.is_file() or source.suffix.lower() not in EXTENSIONS:
            raise SystemExit(f'Reference {reference!r} is not a PNG/JPEG/WebP file. Nothing was sent.')
        target = f'{number}_{re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem)[:40] or "reference"}{source.suffix.lower()}'
        shutil.copyfile(source, folder / target)
        order.append(target)
    (folder / 'prompt.txt').write_text(prompt + '\n', encoding='utf-8')
    (folder / 'package.json').write_text(json.dumps({'order': order}), encoding='utf-8')
    return folder


def size(value) -> str:
    """`[1122, 1402]` -> `1122x1402`; anything else is shown as it is."""
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(n, int) for n in value):
        return f'{value[0]}x{value[1]}'
    return str(value)


def seconds(value) -> str:
    return f'{value:.0f} s' if isinstance(value, (int, float)) else '?'


def report(payload: dict, code: int) -> tuple[list[str], int]:
    """Human lines + exit code for a package_run outcome; never suggests a rerun once something may have been sent."""
    runs = payload.get('runs') or []
    run = runs[0] if runs else {}
    run_id = run.get('run_id')
    # The interpreter that imports the runtime (this one, after the hand-over), not whatever `python3` is first on PATH.
    recover = ' '.join(shlex.quote(part) for part in (sys.executable, str(SKILL_SCRIPTS / 'headless.py'), 'resume', '--run-id', run_id)) if run_id else None
    if run.get('downloaded'):                                                  # the image is on disk whatever else failed
        original = run.get('original_file') or {}
        path = original.get('path') or run.get('download_file')
        phases = run.get('phase_seconds') or {}
        timing = ''
        if 'intent' in phases and 'final_complete' in phases:
            timing = f", generation {seconds(phases['final_complete'] - phases['intent'])}"
        lines = [f'Image saved: {path}',
                 f'  {size(original.get("dimensions") or (run.get("image_dimensions") or [None])[0])}  run {run_id}  1 Send{timing}']
        if run.get('library_verified'):
            lines.append('  also confirmed in your ChatGPT Images library')
        elif code != 0:                                                         # only the optional library check failed
            lines.append(f'  Library check did not pass ({run.get("reason") or "not confirmed"}); the image itself is saved. Do not run this again.')
        return lines, 0
    reason = payload.get('error') or run.get('error') or run.get('reason') or 'the run did not finish'
    state = run.get('send_state')
    lines = [f'No image saved: {reason}.']
    if payload.get('retry_safe') is True or (not runs and payload.get('retry_safe', True)):
        lines.append('  Nothing was sent, so running the same command again after fixing the cause is safe.')
    else:
        lines.append(f'  A request may already have been sent (send_state={state}). Do NOT run this again: that would be a second request.')
        if recover:
            lines.append(f'  Check or finish it read-only: {recover}')
    return lines, 2


class Progress:
    """Turn the runner's single-line JSON events into short progress bullets on stderr."""

    LABELS = {'submission_confirmed': 'Send confirmed, waiting for the image'}

    def __init__(self):
        self.buffer = ''

    def write(self, text):
        self.buffer += text
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            with contextlib.suppress(ValueError):
                event = json.loads(line).get('event')
                if event in self.LABELS:
                    print(f'  - {self.LABELS[event]}', file=sys.stderr, flush=True)
        return len(text)

    def flush(self):
        pass


def run(args) -> int:
    reexec_in_runtime()
    sys.path.insert(0, str(SKILL_SCRIPTS))
    import package_run
    from package_run import headless
    prompt = (sys.stdin.read() if args.prompt == ['-'] else ' '.join(args.prompt)).strip()
    out_dir = Path(args.out).expanduser() if args.out else Path.cwd()          # the terminal's / agent's working directory unless told
    check_folder(out_dir)
    check_browser(args.browser)
    with tempfile.TemporaryDirectory(prefix='imagine-') as scratch:
        package = build_package(prompt, args.ref, Path(scratch), args.name)
        state = Path(scratch) / 'dry-run-state' if args.dry_run else headless.DEFAULT_STATE
        namespace = argparse.Namespace(state_dir=state, concurrency=1, wait_seconds=args.wait_seconds, verify_library=args.verify_library,
                                       pause_idle_worker=False, effort=None, browser=args.browser)
        receipts = []
        try:
            receipts = package_run.create_runs(state, [package], out_dir, browser=args.browser)
            if args.dry_run:
                data = receipts[0].data
                print(f'Dry run: the package is valid. Would send 1 request with {len(args.ref)} reference(s) in {args.browser} and save to {out_dir}.')
                print(f'  prompt ({len(prompt)} chars), references: {[r["filename"] for r in data.get("references", [])]}; nothing was sent, no browser started.')
                return 0
            print(f'Sending 1 request to ChatGPT Images, run {receipts[0].data["run_id"]} (usually 1-3 minutes)...', file=sys.stderr, flush=True)
            with contextlib.redirect_stdout(Progress()):
                payload = asyncio.run(package_run.run_all(namespace, receipts))
            code = 0 if package_run.succeeded(payload['runs'][0], args.verify_library) else 2
        except (Exception, KeyboardInterrupt) as exc:          # Ctrl-C after the click leaves `intent`: batch_failure makes it `unknown`
            error = exc.code if isinstance(exc, headless.ResearchError) else type(exc).__name__
            payload, code = package_run.batch_failure(error, receipts), 2
    lines, code = report(payload, code)
    print('\n'.join(lines))
    if args.json:
        print(json.dumps(payload, indent=2))
    return code


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('prompt', nargs='+', help='what to draw, used exactly as written; `-` reads it from stdin')
    p.add_argument('--ref', action='append', default=[], metavar='IMAGE', help='reference image (PNG/JPEG/WebP), at most two, in prompt order')
    p.add_argument('--out', default=None, metavar='DIR', help='folder for the saved file (default: the current directory); never overwrites')
    p.add_argument('--name', help='file name stem (default: from the prompt words)')
    p.add_argument('--wait-seconds', type=float, default=480, help='how long to wait for the image (default 480, max 1200); an agent host that kills a command after 10 minutes needs a value below its limit')
    p.add_argument('--browser', choices=BROWSER_KINDS, default='cloakbrowser',
                   help='cloakbrowser (default) or chrome: plain Google Chrome in a profile of its own, signed in once with `runtime.py login --browser chrome`')
    p.add_argument('--verify-library', action='store_true', help='also confirm the image appears in the ChatGPT Images library (slower)')
    p.add_argument('--json', action='store_true', help='also print the full machine-readable result')
    p.add_argument('--dry-run', action='store_true', help='validate and show the plan; no browser, no Send')
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if not 0 < args.wait_seconds <= 1200:
        raise SystemExit('--wait-seconds must be between 0 and 1200')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
