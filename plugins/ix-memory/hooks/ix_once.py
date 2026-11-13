# Copyright 2026 Ix Infrastructure Inc.

"""Run each Codex event once, when two copies of the Ix hooks are registered for it.

The plugin's own `hooks/hooks.json` and an older install's entries in
`~/.codex/hooks.json` can both be live: Codex refreshes its copy of the plugin
when the plugin is installed again, not when the installer runs, and a
project's `.codex/hooks.json` can repeat the home one. Codex starts every
matching handler for an event at once and hands each the same bytes on stdin
(checked against Codex 0.162.1), so the first copy to claim those bytes runs and
the rest exit without output.
"""

from __future__ import annotations

import hashlib
import os
import time

import ix_state

# The copies of one event start together, milliseconds apart. Identical bytes
# further apart are a new event: with no per-run field in the input (a session
# resumed twice the same way, say), a long window would swallow the second one
# even with a single copy registered. A marker older than this is a leftover.
CLAIM_WINDOW_SECONDS = 10
_PRUNE_EVERY_SECONDS = 600


def first_delivery(raw: str) -> bool:
    """True unless another copy of the hooks already took this exact event.

    Any failure answers True: running twice is better than not running.
    """
    events = ix_state.CACHE_DIR / "events"
    if not ix_state._private_dir(events):
        return True
    marker = events / hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:32]
    try:
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        try:
            fresh = time.time() - marker.stat().st_mtime < CLAIM_WINDOW_SECONDS
        except OSError:
            return True
        if fresh:
            return False
        try:
            os.utime(marker)
        except OSError:
            pass
        return True
    except OSError:
        return True
    _prune(events)
    return True


def _prune(events) -> None:
    stamp = events / ".pruned"
    now = time.time()
    try:
        if now - stamp.stat().st_mtime < _PRUNE_EVERY_SECONDS:
            return
    except OSError:
        pass
    try:
        stamp.touch()
        for entry in os.scandir(events):
            if entry.name != ".pruned" and now - entry.stat().st_mtime > CLAIM_WINDOW_SECONDS:
                os.unlink(entry.path)
    except OSError:
        pass
