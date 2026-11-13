# Copyright 2026 Ix Infrastructure Inc.

"""Running `ix`: executable resolution, the cmd.exe shim guard, and output parsing.

Everything that starts a process for a hook goes through `run_command`, which
is what applies the Windows resolution and refuses arguments a `.cmd` shim
would hand to a shell.
"""

from __future__ import annotations

import concurrent.futures
import functools
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable


def resolve_ix_argv(argv: list[str]) -> list[str]:
    """Replace a leading bare `ix` with the resolved path to the executable.

    On Windows the installer puts an `ix.CMD` shim on PATH. `subprocess` there
    hands the command to CreateProcess, which — unlike the shell — does not
    consult PATHEXT, so a bare "ix" matches no file on disk and every call dies
    with `FileNotFoundError: [WinError 2]`. The same command typed into
    PowerShell works, and `shutil.which("ix")` finds `ix.CMD` quite happily,
    which is what made this look like the CLI was fine and only the hooks were
    broken.

    `shutil.which` DOES apply PATHEXT, so resolving through it and passing the
    full path is the fix. POSIX is unaffected: `which` returns the same path the
    kernel would have found anyway.

    Falls back to the bare name when `ix` is not installed, so the caller still
    gets the ordinary "not found" error rather than a confusing one from here.
    """
    if not argv or argv[0] != "ix":
        return argv
    return [_ix_executable(), *argv[1:]]


@functools.lru_cache(maxsize=1)
def _ix_executable() -> str:
    resolved = shutil.which("ix")
    # Not if it came from the working directory. On Windows shutil.which searches
    # the current directory *first* unless NoDefaultCurrentDirectoryInExePath is
    # set, which by default it is not -- so a repository committing an `ix.bat`
    # at its root would be run by all five hooks the moment the repo is opened.
    # Passing `path=` does not help: CPython inserts os.curdir whenever the
    # command has no directory part, explicit path or not.
    #
    # That is not a hole this file inherited, it is one resolution would create.
    # A bare "ix" is immune, because CreateProcess only ever appends `.exe` --
    # which is also why the CLI could not be launched at all before this change,
    # and why gaining PATHEXT means gaining `.bat`, `.cmd`, `.py` and the rest of
    # it. A PATH hit is absolute; the current-directory hit is not.
    #
    # Falling back to the bare name rather than raising keeps the "not installed"
    # path: on Windows the caller gets the ordinary not-found, which is exactly
    # what it got before, and on POSIX which never returns a relative path.
    if resolved is None or not os.path.isabs(resolved):
        return "ix"
    return resolved


# Resolving the executable is what makes the hooks work on Windows, and it is
# also what makes them reachable: `ix.CMD` is a batch file, and CreateProcess
# runs those by handing the command line to `cmd.exe /c`. Every argument is then
# parsed by a shell before the CLI sees it -- and subprocess quotes an argument
# only when it contains whitespace, so `a&whoami` splits into two commands while
# `a & whoami` does not. `%VAR%` is expanded either way.
#
# These arguments are not trusted: build_write_warning takes the path of a
# file the model is about to write, and build_search_message takes a pattern lifted
# out of a command the model ran. Before the resolution above, a bare "ix" could
# not be launched on Windows at all, so this was unreachable there; afterwards it
# is the ordinary path.
#
# Quoting correctly for cmd.exe is the trap CVE-2024-24576 was about, so this
# refuses the input rather than trying to escape it.
_CMD_SHIM_SUFFIXES = (".cmd", ".bat")
_CMD_METACHARACTERS = frozenset('&|<>^"%!\r\n')


def unsafe_for_cmd_shim(argv: list[str]) -> str:
    """The cmd.exe metacharacters in `argv`, if argv[0] routes through one."""
    if not argv or not argv[0].lower().endswith(_CMD_SHIM_SUFFIXES):
        return ""
    found: set[str] = set()
    for arg in argv:
        found |= set(arg) & _CMD_METACHARACTERS
    return " ".join(repr(char) for char in sorted(found))


def run_command(
    argv: list[str], cwd: str | Path | None = None, timeout: int = 10
) -> subprocess.CompletedProcess[str] | None:
    resolved = resolve_ix_argv(argv)
    if unsafe_for_cmd_shim(resolved):
        # None is the existing "this did not run" answer and every caller already
        # handles it. The hooks are best-effort context, so declining one query is
        # a missed suggestion; running it is arbitrary execution.
        return None
    try:
        return subprocess.run(
            resolved,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    # ValueError too: an embedded NUL raises it out of subprocess on every
    # platform, and it is not an OSError.
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ix_available() -> bool:
    return shutil.which("ix") is not None


def extract_json_fragment(text: str) -> str | None:
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    try:
        json.loads(stripped)
        return stripped
    except json.JSONDecodeError:
        pass

    lines = text.splitlines()
    for index, line in enumerate(lines):
        candidate = "\n".join(lines[index:]).strip()
        if not candidate:
            continue
        first = candidate[0]
        if first not in {"{", "["}:
            continue
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    return None


def parse_json_output(text: str) -> dict | list | None:
    fragment = extract_json_fragment(text)
    if not fragment:
        return None
    try:
        return json.loads(fragment)
    except json.JSONDecodeError:
        return None


def run_ix_json(
    argv: list[str], cwd: str | Path | None = None, timeout: int = 10
) -> dict | list | None:
    result = run_command(argv, cwd=cwd, timeout=timeout)
    if not result or result.returncode != 0:
        return None
    return parse_json_output(result.stdout)


def _ix_text_from_stdout(stdout: str | None) -> str | None:
    fragment = extract_json_fragment(stdout or "")
    return fragment or (stdout or "").strip() or None


def run_ix_text(
    argv: list[str], cwd: str | Path | None = None, timeout: int = 10
) -> str | None:
    result = run_command(argv, cwd=cwd, timeout=timeout)
    if not result or result.returncode != 0:
        return None
    return _ix_text_from_stdout(result.stdout)


def run_parallel(
    calls: list[tuple[str, Callable[..., object], list[str], int]], cwd: str | Path | None
) -> dict[str, object]:
    """Each `(name, runner, argv, timeout)` at once; `{name: runner's answer}`."""
    results: dict[str, object] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls) or 1) as executor:
        future_map = {
            executor.submit(runner, argv, cwd=cwd, timeout=timeout): name
            for name, runner, argv, timeout in calls
        }
        for future in concurrent.futures.as_completed(future_map):
            results[future_map[future]] = future.result()
    return results
