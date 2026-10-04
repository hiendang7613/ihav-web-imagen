"""One browser per profile: an OS file lock the kernel releases on exit or crash."""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from .core import ResearchError


@contextmanager
def profile_lock(state: Path):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / 'profile.lock'
    with path.open('a+') as stream:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResearchError('profile_busy', 'Another run or login window owns this profile') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
