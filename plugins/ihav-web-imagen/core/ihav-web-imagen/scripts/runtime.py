"""Set up, sign in and check the browser runtime. None of these commands sends a prompt.

    runtime.py setup                    # one time: a private venv with the pinned packages, and the CloakBrowser Chromium
    runtime.py login                    # one time: a visible browser window; sign in to ChatGPT yourself, then close it
    runtime.py login --site all         # one tab per known web chat (see sites.py); sign in to the ones you use
    runtime.py login --browser chrome   # the same for plain Google Chrome in a profile of its own
    runtime.py doctor                   # what is installed and what is missing; changes nothing

Everything lives in one folder: ~/Library/Application Support/ihav-web-imagen on macOS,
${XDG_DATA_HOME:-~/.local/share}/ihav-web-imagen elsewhere, or IHAV_WEB_IMAGEN_HOME. The CloakBrowser Chromium is cached
by CloakBrowser itself in ~/.cloakbrowser. Remove both to uninstall.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from chatgpt_web.paths import default_state, venv_python  # noqa: E402  (standard library only)

REQUIREMENTS = SCRIPTS / 'runtime-requirements.txt'
MIN_PYTHON = (3, 11)
CANDIDATES = ('python3.12', 'python3.13', 'python3.11', 'python3')


def python_version(executable: str) -> tuple[int, int] | None:
    try:
        out = subprocess.run([executable, '-c', 'import sys; print(*sys.version_info[:2])'],
                             capture_output=True, text=True, timeout=20, check=True).stdout.split()
        return int(out[0]), int(out[1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def base_python() -> str | None:
    """An interpreter new enough for the runtime: this one, else the first suitable one on PATH."""
    if sys.version_info[:2] >= MIN_PYTHON:
        return sys.executable
    for name in CANDIDATES:
        found = shutil.which(name)
        version = python_version(found) if found else None
        if version and version >= MIN_PYTHON:
            return found
    return None


def step(label: str, command: list[str]) -> str:
    print(f'- {label}', file=sys.stderr, flush=True)
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        tail = '\n'.join((done.stderr or done.stdout).strip().splitlines()[-12:])
        raise SystemExit(f'Setup failed while trying to {label.lower()} (exit {done.returncode}). Nothing was sent.\n{tail}')
    return done.stdout


def installed(python: Path) -> bool:
    if not python.is_file():
        return False
    probe = 'import cloakbrowser, playwright, pydantic'
    return subprocess.run([str(python), '-c', probe], capture_output=True).returncode == 0


def setup(state: Path) -> dict:
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    python = venv_python(state)
    if not installed(python):
        base = base_python()
        if base is None:
            raise SystemExit('Setup needs Python 3.11 or newer (python3.12 is a good choice). Install it, then run setup again. Nothing was sent.')
        if not python.is_file():
            step('Create the private venv', [base, '-m', 'venv', str(state / 'venv')])
        step('Install the pinned packages (cloakbrowser, playwright, pydantic)',
             [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', str(REQUIREMENTS)])
    binary = step('Download the CloakBrowser Chromium (once; about 200 MB; reused if already cached)',
                  [str(python), '-m', 'cloakbrowser', 'install']).strip().splitlines()[-1]
    config = state / 'config.json'
    saved = json.loads(config.read_text()) if config.is_file() else {'schema_version': 1, 'headless': True}
    saved['browser_binary'] = binary
    config.write_text(json.dumps(saved, indent=2, sort_keys=True) + '\n')
    os.chmod(config, 0o600)
    return {'ready': True, 'state_dir': str(state), 'python': str(python), 'browser_binary': binary,
            'signed_in': 'unknown', 'next': f'{shlex_join([sys.executable, str(Path(__file__).resolve()), "login"])}  (one time; sends nothing)'}


def shlex_join(parts: list[str]) -> str:
    return ' '.join(shlex.quote(part) for part in parts)


def login(state: Path, browser: str, site: list[str] | None = None) -> int:
    python = venv_python(state)
    if not installed(python):
        raise SystemExit(f'Run setup first: {shlex_join([sys.executable, str(Path(__file__).resolve()), "setup"])}. Nothing was sent.')
    command = [str(python), str(SCRIPTS / 'headless.py'), 'login', '--state-dir', str(state)]
    if browser == 'chrome':
        command += ['--browser', 'chrome']
    for name in site or []:
        command += ['--site', name]
    return subprocess.run(command).returncode


def doctor(state: Path) -> dict:
    python = venv_python(state)
    config = state / 'config.json'
    binary = None
    if config.is_file():
        binary = json.loads(config.read_text()).get('browser_binary')
    report = {
        'state_dir': str(state),
        'venv': python.is_file(),
        'packages': installed(python),
        'browser_binary': binary,
        'browser_binary_present': bool(binary) and Path(binary).is_file(),
        'cloakbrowser_profile': (state / 'profile').is_dir(),
        'chrome_profile': (state / 'chrome-profile').is_dir(),
        'signed_in': 'unknown: only a real run checks it, and it stops before any Send when signed out',
    }
    report['ready'] = report['packages'] and report['browser_binary_present']
    if not report['ready']:
        report['next'] = shlex_join([sys.executable, str(Path(__file__).resolve()), 'setup'])
    elif not report['cloakbrowser_profile'] and not report['chrome_profile']:
        report['next'] = shlex_join([sys.executable, str(Path(__file__).resolve()), 'login'])
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('setup', 'login', 'doctor'))
    parser.add_argument('--browser', choices=('cloakbrowser', 'chrome'), default='cloakbrowser', help='login only')
    parser.add_argument('--site', action='append', default=None, help='login only: a site id (chatgpt, gemini, lechat, qwen, grok, perplexity, metaai, zai, kimi) or all; repeatable')
    parser.add_argument('--state-dir', type=Path, default=None, help='default: see above')
    args = parser.parse_args(argv)
    state = (args.state_dir or default_state()).expanduser()
    if args.command == 'login':
        return login(state, args.browser, args.site)
    result = setup(state) if args.command == 'setup' else doctor(state)
    print(json.dumps(result, indent=2))
    return 0 if result.get('ready') or args.command == 'doctor' else 1


if __name__ == '__main__':
    sys.exit(main())
