"""Where ihav-web-imagen keeps its state. Standard library only, so the launcher can import it before the venv exists."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def default_state() -> Path:
    """The private venv, browser profiles and run receipts. IHAV_WEB_IMAGEN_HOME overrides it."""
    named = os.environ.get('IHAV_WEB_IMAGEN_HOME')
    if named:
        return Path(named).expanduser()
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/ihav-web-imagen'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'ihav-web-imagen'


def venv_python(state: Path) -> Path:
    return state / 'venv' / 'bin' / 'python'
