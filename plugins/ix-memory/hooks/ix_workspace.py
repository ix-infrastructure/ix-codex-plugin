# Copyright 2026 Ix Infrastructure Inc.

"""Which project a hook payload belongs to: its git root, and never `$HOME`."""

from __future__ import annotations

import os
from pathlib import Path

from ix_proc import run_command


# Also the auto-map guard's first call, so it is bounded with
# ix_automap.AUTO_MAP_STATUS_TIMEOUT_SECONDS below the Stop hook's 10 s timeout.
GIT_TOPLEVEL_TIMEOUT_SECONDS = 3


def _home() -> Path | None:
    try:
        return Path.home().resolve()
    except (RuntimeError, KeyError, OSError):
        return None


def _canonical(path: str | Path) -> str:
    """A spelling of `path` two equal directories always share, on every OS.

    normcase folds case and separators on Windows, where `git rev-parse` prints
    `C:/Users/...` while Python hands back `C:\\Users\\...`.
    """
    return os.path.normcase(str(Path(path).resolve()))


def _is_home(path: str | Path) -> bool:
    home = _home()
    return home is not None and _canonical(path) == _canonical(home)


def git_toplevel(path: str | Path | None) -> Path | None:
    """`git -C <path> rev-parse --show-toplevel`, or None outside a work tree."""
    if not path:
        return None
    result = run_command(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        timeout=GIT_TOPLEVEL_TIMEOUT_SECONDS,
    )
    if not result or result.returncode != 0:
        return None
    top = result.stdout.strip()
    if not top:
        return None
    try:
        return Path(top).resolve()
    except (OSError, ValueError):
        return None


def find_workspace_root(cwd: str | None) -> Path | None:
    """The project the hook payload's `cwd` belongs to, or None for "no project".

    The git root of `cwd` first. This used to look for `.codex/hooks.json`
    before any `.git` -- and a `--home` install puts exactly that file in
    `~/.codex/`, so every hook in every repository under `$HOME` resolved to
    `$HOME` itself and ran its `ix` queries (and its `ix map`) from there.

    Never `$HOME`: a home directory is not a project, even when it is a dotfiles
    repository. Outside git, the directory itself is the best answer there is.
    """
    try:
        start = Path(cwd or os.getcwd()).resolve()
    except (OSError, ValueError):
        return None
    top = git_toplevel(start)
    if top is not None and not _is_home(top):
        return top
    if _is_home(start):
        return None
    return start
