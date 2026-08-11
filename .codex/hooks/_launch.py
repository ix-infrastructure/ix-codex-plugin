#!/usr/bin/env python3
"""Locate and run an Ix Codex hook, without needing a shell.

Every entry in ``hooks.json`` used to be a ``/bin/sh -lc '...'`` one-liner that
walked up from ``$PWD`` looking for ``.codex/hooks/<name>.py``, fell back to the
copy under ``$HOME``, and ``exec``'d ``python3`` on whichever it found.

Native Windows has no ``/bin/sh``, so on Windows every hook failed to launch at
all — before any of the hook's own code ran. That is the outer half of
ix-infrastructure/Ix#383; the inner half (``subprocess`` could not find
``ix.CMD`` without ``PATHEXT``) is fixed separately in ``common.py``, and is
unreachable until this one is fixed too.

This does the same search in Python, which the machine already needs — the hooks
themselves are Python and so is the installer. The installer points ``hooks.json``
at this file with an absolute path and an absolute interpreter, so no ``python3``
has to be on ``PATH`` either.

Not a subprocess: the hook is run in-process with ``runpy`` so it inherits stdin,
stdout and stderr exactly as ``exec`` gave it to them. Codex speaks to hooks over
stdio, so an extra process layer would have to forward all three faithfully, and
getting that wrong is silent.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def find_hook(name: str, start: Path | None = None) -> Path | None:
    """The nearest `.codex/hooks/<name>.py`, walking up from `start`.

    Project-local hooks win over the ones installed under `$HOME`, which is what
    lets a repo pin its own behaviour — the same precedence the shell version
    had. Returns None when neither exists; the caller treats that as "nothing to
    do", not as an error, because a workspace with no Ix hooks installed is a
    normal state and Codex must not be blocked by it.
    """
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".codex" / "hooks" / f"{name}.py"
        if candidate.is_file():
            return candidate

    home = Path(os.path.expanduser("~")) / ".codex" / "hooks" / f"{name}.py"
    return home if home.is_file() else None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: _launch.py <hook-name>", file=sys.stderr)
        return 2

    name = argv[1]
    hook = find_hook(name)
    if hook is None:
        # Matches the shell version's trailing `exit 0`. A missing hook must
        # never fail the Codex turn.
        return 0

    # argv[0] is what the hook sees as its own path, and sys.argv is trimmed to
    # the hook's own arguments, so a hook cannot tell it was launched this way.
    sys.argv = [str(hook), *argv[2:]]
    try:
        runpy.run_path(str(hook), run_name="__main__")
    except SystemExit as exc:  # the hook called sys.exit(); that is its status
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
