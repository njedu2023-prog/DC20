"""Local compare-and-swap state store; production Git CAS remains a separate gate."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_state(path: Path) -> tuple[dict | None, str | None]:
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("state path has a symlink")
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("state must be an object")
    return data, digest(raw)


def compare_and_swap(path: Path, value: dict, expected_sha256: str | None) -> str:
    """Never overwrite a concurrently changed file or follow a symlink."""
    import fcntl
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("state path has a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(lock_path, flags, 0o600), "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _, actual = read_state(path)
        if actual != expected_sha256:
            raise ValueError("state CAS conflict; reread and validate, do not overwrite")
        raw = encoded(value)
        if digest(raw) == actual:
            return actual
        descriptor, temporary = tempfile.mkstemp(prefix=".forward-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return digest(raw)
