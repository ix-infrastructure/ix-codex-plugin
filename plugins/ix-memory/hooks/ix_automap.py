# Copyright 2026 Ix Infrastructure Inc.

"""The guarded automatic `ix map`: which repository, how often, and only if already mapped."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ix_proc import parse_json_output, resolve_ix_argv, run_command, unsafe_for_cmd_shim
from ix_state import CACHE_DIR, _read_text, _read_timestamp, _root_key, _write_text
from ix_workspace import _is_home, git_toplevel


# At most one automatic `ix map` per repository in this window, whichever hook
# asks first. Ix holds its own per-workspace map lock, so this is about cost,
# not correctness: a Stop hook fires on every turn.
AUTO_MAP_DEBOUNCE_SECONDS = 120
# The auto-map guard runs inside the Stop hook (10 s timeout in hooks.json), so
# the two synchronous calls it makes are bounded well below that together: this
# one and ix_workspace.GIT_TOPLEVEL_TIMEOUT_SECONDS.
AUTO_MAP_STATUS_TIMEOUT_SECONDS = 4


# ── Guarded automatic map ────────────────────────────────────────────────────
#
# The hooks used to run `ix map` from wherever they happened to resolve -- with a
# `--home` install that was `$HOME` on every turn -- and `ix map <file>` after
# every write, which the CLI rejects outright ("Map path is not a directory").
# An automatic map now runs only for a git repository that is already mapped,
# never for `$HOME`, at most once per AUTO_MAP_DEBOUNCE_SECONDS per repository,
# and always detached.


def auto_map_root(project_dir: str | Path | None) -> Path | None:
    """The git root of the host's project directory, unless that is `$HOME`."""
    if not project_dir:
        return None
    top = git_toplevel(project_dir)
    if top is None or _is_home(top):
        return None
    return top


def _auto_map_stamp_path(root: str | Path) -> Path:
    return CACHE_DIR / f"auto-map-{_root_key(root)}.stamp"


def _claim_auto_map(root: Path) -> bool:
    """Take this repository's debounce slot; False if it is already taken.

    Claimed before the status check, not after the map starts, so a repository
    that is not mapped costs one `ix status` per window rather than one per
    turn. A slot that cannot be recorded is not taken: without the stamp every
    turn would map.
    """
    stamp = _auto_map_stamp_path(root)
    raw = _read_text(stamp)
    if raw is not None:
        age = time.time() - _read_timestamp(raw.strip())
        if 0 <= age < AUTO_MAP_DEBOUNCE_SECONDS:
            return False
    return _write_text(stamp, str(time.time()))


def project_is_mapped(root: Path) -> bool:
    """`ix status --root <root>` says this workspace already has a graph.

    Anything short of an explicit `graphCompleted: true` -- a failure, a
    timeout, output that is not JSON -- is "no": an automatic map must never be
    what creates a workspace.
    """
    result = run_command(
        ["ix", "status", "--format", "json", "--root", str(root)],
        cwd=root,
        timeout=AUTO_MAP_STATUS_TIMEOUT_SECONDS,
    )
    if not result or result.returncode != 0:
        return False
    payload = parse_json_output(result.stdout or "")
    return isinstance(payload, dict) and payload.get("graphCompleted") is True


def _detached_popen_kwargs() -> dict:
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
        return {"creationflags": flags}
    return {"start_new_session": True}


def spawn_background_ix_map(root: Path) -> bool:
    """Start `ix map <root> --silent` detached, from `root`, marked as automatic.

    IX_AUTO_MAP=1 tells the CLI this map was not asked for by a person, which is
    what lets it decline to push a whole repository at a remote backend.
    """
    argv = resolve_ix_argv(["ix", "map", str(root), "--silent"])
    if unsafe_for_cmd_shim(argv):
        return False
    try:
        subprocess.Popen(
            argv,
            cwd=str(root),
            env={**os.environ, "IX_AUTO_MAP": "1"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_detached_popen_kwargs(),
        )
    except (OSError, ValueError):
        return False
    return True


def request_auto_map(project_dir: str | Path | None) -> bool:
    """Refresh the graph for `project_dir`'s repository if every guard allows it.

    `project_dir` must come from the host's payload (its `cwd`), never from where
    this file lives. Returns whether a map was started.
    """
    root = auto_map_root(project_dir)
    if root is None:
        return False
    if not _claim_auto_map(root):
        return False
    if not project_is_mapped(root):
        return False
    return spawn_background_ix_map(root)
