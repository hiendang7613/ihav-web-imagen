"""State location, configuration and small helpers shared by the vendored adapter and the image scripts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from .paths import default_state


DEFAULT_STATE = default_state()


class ResearchError(Exception):
    """A failure with a stable code. The name is kept from the upstream adapter so its call sites stay unchanged."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RuntimeConfig(StrictModel):
    schema_version: Literal[1] = 1
    browser_binary: str | None = None      # None: CloakBrowser's own downloaded Chromium
    headless: bool = True


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ResearchError('invalid_json', 'Expected a JSON object')
    return value


def saved_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme != 'https' or parsed.netloc != 'chatgpt.com'
        or not re.fullmatch(r'/c/[A-Za-z0-9_-]{8,}', parsed.path)
        or parsed.query or parsed.fragment
    ):
        raise ResearchError('invalid_conversation_url', 'Expected a saved chatgpt.com/c/... URL')
    return value


def load_config(state: Path) -> RuntimeConfig:
    """The saved configuration, or the defaults when setup has not written one (a missing browser fails at launch)."""
    path = state / 'config.json'
    if not path.is_file():
        return RuntimeConfig()
    return RuntimeConfig.model_validate(read_object(path))
