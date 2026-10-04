#!/usr/bin/env python3
"""Developer install of ihav-web-imagen from a clone of this repo (stdlib only). Users want `install.sh` (native plugin install) instead.

    python3 install.py                 # every host whose CLI or config folder is present
    python3 install.py --codex         # Codex:        ~/.codex/skills/ihav-web-imagen  -> this repo's skill (symlink: git pull updates it)
    python3 install.py --claude        # Claude Code:  ~/.claude/skills/ihav-web-imagen/SKILL.md (user-invoked wrapper) -> /ihav-web-imagen
    python3 install.py --uninstall     # remove exactly what this script created
    python3 install.py --doctor        # check the browser runtime the skill needs (v0.1: not bundled)
    python3 install.py --dry-run       # show what would change

It never overwrites anything it did not create. This is the short-command, edit-in-place setup for people working on the repo; everyone
else should run `install.sh`, which uses each host's own plugin marketplace.
"""

from __future__ import annotations

import argparse
import json
import importlib.util
import os
import shutil
import sys
from pathlib import Path

NAME = 'ihav-web-imagen'
ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / 'plugins' / NAME
SKILL = PLUGIN / 'core' / NAME
TEMPLATE = PLUGIN / 'claude' / 'skills' / 'imagine' / 'SKILL.md'
MARKER = '<!-- installed by ihav-web-imagen install.py -->'


def render_claude_skill(template: str, *, name: str, root: str, invocation: str) -> str:
    """The plugin wrapper, re-targeted at a fixed install: skill name, script root and the slash command shown to the user."""
    text = template.replace('name: imagine', f'name: {name}', 1).replace('${CLAUDE_PLUGIN_ROOT}', root)
    return text.replace('/ihav-web-imagen:imagine', invocation)


def claude_target(home: Path) -> Path:
    return home / '.claude' / 'skills' / NAME / 'SKILL.md'


def codex_target(home: Path) -> Path:
    return home / '.codex' / 'skills' / NAME


def personal_claude_skill() -> str:
    body = render_claude_skill(TEMPLATE.read_text(encoding='utf-8'), name=NAME, root=str(PLUGIN), invocation=f'/{NAME}')
    return body.rstrip('\n') + '\n\n' + MARKER + '\n'


def install_codex(home: Path, dry_run: bool, force: bool) -> str:
    target = codex_target(home)
    if target.is_symlink() and target.resolve() == SKILL.resolve():
        return f'Codex: already installed ({target} -> {SKILL})'
    if target.exists() or target.is_symlink():
        if target.is_symlink() and force:
            if not dry_run:
                target.unlink()
        else:
            raise SystemExit(f'Codex: {target} already exists and is not this repo\'s link; remove it yourself or pass --force (replaces links only).')
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(SKILL, target_is_directory=True)
    return f'Codex: {"would link" if dry_run else "linked"} {target} -> {SKILL}   (use: ${NAME} <what to draw>)'


def install_claude(home: Path, dry_run: bool, force: bool) -> str:
    target = claude_target(home)
    content = personal_claude_skill()
    if target.exists():
        current = target.read_text(encoding='utf-8')
        if current == content:
            return f'Claude Code: already installed ({target})'
        if MARKER not in current:                                     # never overwrite what this installer did not write, even with --force
            raise SystemExit(f'Claude Code: {target} exists and was not written by this installer; move it away yourself.')
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    return f'Claude Code: {"would write" if dry_run else "wrote"} {target}   (use: /{NAME} <what to draw>)'


def uninstall(home: Path, dry_run: bool) -> list[str]:
    out = []
    codex = codex_target(home)
    if codex.is_symlink() and codex.resolve() == SKILL.resolve():
        if not dry_run:
            codex.unlink()
        out.append(f'Codex: {"would remove" if dry_run else "removed"} {codex}')
    claude = claude_target(home)
    if claude.is_file() and MARKER in claude.read_text(encoding='utf-8'):
        if not dry_run:
            claude.unlink()
            with_parent = claude.parent
            if not any(with_parent.iterdir()):
                with_parent.rmdir()
        out.append(f'Claude Code: {"would remove" if dry_run else "removed"} {claude}')
    return out or ['nothing of ours was installed']


def doctor() -> list[str]:
    """What the skill needs at run time. Returns human lines; the last line says whether it can run."""
    import subprocess
    runtime = SKILL / 'scripts' / 'runtime.py'
    lines = [f'{"ok     " if SKILL.is_dir() else "MISSING"} skill in this clone: {SKILL}']
    if not runtime.is_file():
        return lines + ['not ready: the skill folder is incomplete']
    done = subprocess.run([sys.executable, str(runtime), 'doctor'], capture_output=True, text=True)
    try:
        report = json.loads(done.stdout)
    except ValueError:
        return lines + [f'not ready: runtime.py doctor failed: {(done.stderr or done.stdout).strip()[-300:]}']
    lines += [f'{"ok     " if report["packages"] else "MISSING"} browser runtime venv: {report["state_dir"]}/venv',
              f'{"ok     " if report["browser_binary_present"] else "MISSING"} CloakBrowser Chromium: {report["browser_binary"] or "not installed"}',
              f'ok      python {sys.version.split()[0]}']
    lines.append('ready to run (sign in once with runtime.py login if you have not)' if report['ready']
                 else f'not ready: run {report.get("next", "runtime.py setup")} (it sends nothing)')
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--codex', action='store_true')
    parser.add_argument('--claude', action='store_true')
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--doctor', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--force', action='store_true', help='replace an existing symlink to another checkout (never a real file or folder)')
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('IHAV_WEB_IMAGEN_INSTALL_HOME', Path.home())), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.doctor:
        print('\n'.join(doctor()))
        return 0
    if args.uninstall:
        print('\n'.join(uninstall(args.home, args.dry_run)))
        return 0
    hosts = [h for h, flag in (('codex', args.codex), ('claude', args.claude)) if flag]
    if not hosts:                                                    # default: only the hosts that are actually there
        present = {'codex': shutil.which('codex') or (args.home / '.codex').is_dir(), 'claude': shutil.which('claude') or (args.home / '.claude').is_dir()}
        hosts = [h for h in ('codex', 'claude') if present[h]]
        if not hosts:
            raise SystemExit('Neither Codex nor Claude Code was found (no CLI on PATH, no ~/.codex or ~/.claude). Pass --codex or --claude to install anyway.')
    for host in hosts:
        print((install_codex if host == 'codex' else install_claude)(args.home, args.dry_run, args.force))
    print('Open a new session in the host. v0.1 runs only where its browser runtime is installed (README, Status); `--dry-run` sends nothing but needs that runtime too.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
