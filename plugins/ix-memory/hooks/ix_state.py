# Copyright 2026 Ix Infrastructure Inc.

"""The per-user state directory: the hooks' caches and their briefing stamps."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from ix_workspace import _canonical


def _default_state_dir() -> Path:
    """Per-user directory for the hooks' caches and the auto-map debounce stamps.

    Not `$TMPDIR/ix-codex-hooks`: on Linux that is a fixed, world-writable
    `/tmp` path, so a second account on the machine could pre-create it (or
    plant a stamp in it) and every user would read one another's caches.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "")
        if base and os.path.isabs(base):
            return Path(base) / "ix-codex-plugin"
    else:
        base = os.environ.get("XDG_STATE_HOME", "")
        if base and os.path.isabs(base):  # the spec says to ignore a relative one
            return Path(base) / "ix-codex-plugin"
    try:
        home = Path.home()
    except (RuntimeError, KeyError):  # no resolvable home directory
        owner = os.getuid() if hasattr(os, "getuid") else os.environ.get("USERNAME", "user")
        return Path(tempfile.gettempdir()) / f"ix-codex-plugin-{owner}"
    if os.name == "nt":
        return home / "AppData" / "Local" / "ix-codex-plugin"
    return home / ".local" / "state" / "ix-codex-plugin"


CACHE_DIR = _default_state_dir()
STATUS_CACHE_PATH = CACHE_DIR / "ix-status.json"
BRIEFING_CACHE_PATH = CACHE_DIR / "ix-briefing.txt"
PRO_CACHE_PATH = CACHE_DIR / "ix-pro.json"

BRIEFING_TTL_SECONDS = 600


def _private_dir(directory: Path) -> bool:
    """Create `directory` for this user only; False if it cannot be trusted.

    0700 on POSIX, and refused when it already exists under someone else's uid
    -- the case that matters for the temp-dir fallback, where another account
    could have created the path first. Windows ignores the mode; the default
    location there is already inside the user's profile.
    """
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if hasattr(os, "getuid") and directory.stat().st_uid != os.getuid():
            return False
    except OSError:
        return False
    return True


def _write_text(path: Path, text: str) -> bool:
    """Best-effort write. A cache that cannot be written must never fail a hook."""
    if not _private_dir(path.parent):
        return False
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)  # readers never see half a file
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _write_cache(path: Path, payload: dict) -> bool:
    return _write_text(path, json.dumps(payload))


def _read_timestamp(raw: object) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _root_key(root: str | Path) -> str:
    return hashlib.sha256(_canonical(root).encode("utf-8")).hexdigest()[:16]


def _briefing_cache_path(root: str | Path | None) -> Path:
    # Per project: the briefing is about one workspace, so having just seen
    # repo A's must not hold back repo B's for the next ten minutes.
    if root is None:
        return BRIEFING_CACHE_PATH
    return BRIEFING_CACHE_PATH.with_name(
        f"{BRIEFING_CACHE_PATH.stem}-{_root_key(root)}{BRIEFING_CACHE_PATH.suffix}"
    )


def briefing_due(
    root: str | Path | None = None, ttl_seconds: int = BRIEFING_TTL_SECONDS
) -> bool:
    raw = _read_text(_briefing_cache_path(root))
    if raw is None:
        return True
    try:
        last_sent = float(raw.strip())
    except ValueError:
        return True
    return time.time() - last_sent >= ttl_seconds


def mark_briefing_sent(root: str | Path | None = None) -> None:
    _write_text(_briefing_cache_path(root), str(time.time()))
