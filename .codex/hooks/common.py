# Copyright 2026 Ix Infrastructure Inc.

from __future__ import annotations

import concurrent.futures
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable


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

HEALTH_TTL_SECONDS = 30
# Whether @ix/pro is installed changes only when someone installs or removes it,
# and the probe now runs a real command rather than `--help`, so it is cached far
# longer than the health check it used to borrow its TTL from.
PRO_TTL_SECONDS = 3600
# ...but only for a definitive answer. A probe that could not reach a verdict
# (timeout, or a non-zero exit that is not the Pro stub) is still recorded, on a
# short TTL, purely so it cannot spin: without a record the next hook re-probes
# immediately, and since the probe is now the most expensive command in the CLI
# that turns one slow backend into an 8s stall on *every* prompt. Short enough
# that a transient fault still clears on its own within a minute.
PRO_PROBE_BACKOFF_SECONDS = 60
BRIEFING_TTL_SECONDS = 600
# At most one automatic `ix map` per repository in this window, whichever hook
# asks first. Ix holds its own per-workspace map lock, so this is about cost,
# not correctness: a Stop hook fires on every turn.
AUTO_MAP_DEBOUNCE_SECONDS = 120
# The auto-map guard runs inside the Stop hook (10 s timeout in hooks.json), so
# the two synchronous calls it makes are bounded well below that together.
GIT_TOPLEVEL_TIMEOUT_SECONDS = 3
AUTO_MAP_STATUS_TIMEOUT_SECONDS = 4

SHELL_OPERATORS = ("|", "&&", "||", ";", "$(", "`")

# Output redirect: > or >> not preceded by 2 (stderr), < (heredoc), or > (already matched)
WRITE_REDIRECT_RE = re.compile(r"(?<![2<>])>>?\s+([^\s|;&<>]+)")
EDITOR_COMMANDS = frozenset({"vim", "vi", "nvim", "nano", "emacs", "hx", "micro"})
WRITE_SKIP_SUFFIXES = (
    ".bin", ".exe", ".gif", ".gz", ".ico", ".jpeg", ".jpg",
    ".pdf", ".png", ".tar", ".zip", ".lock", ".sum",
)
SEARCH_COMMANDS = {"grep", "rg"}
READ_COMMANDS = {"cat", "head", "tail", "sed", "awk"}
REGEX_META_RE = re.compile(r"[\\^$\[\](){}|*+?]")
SEARCH_VALUE_OPTIONS = {
    "-A",
    "-B",
    "-C",
    "-e",
    "-f",
    "-g",
    "-m",
    "-t",
    "--context",
    "--file",
    "--glob",
    "--max-count",
    "--regexp",
    "--type",
    "--type-add",
}
READ_SKIP_SUFFIXES = (
    ".bin",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".tar",
    ".zip",
)
READ_SKIP_SEGMENTS = (
    "/.git/",
    "/__pycache__/",
    "/build/",
    "/dist/",
    "/generated/",
    "/node_modules/",
)
READ_SKIP_BASENAMES = {
    "cargo.lock",
    "go.sum",
    "package-lock.json",
    "pnpm-lock.yaml",
    "skill.md",
    "yarn.lock",
}


def load_plugin_version() -> dict | None:
    """Load the version metadata written by the installer into .codex/ix-plugin-version.json."""
    version_file = Path(__file__).parent.parent / "ix-plugin-version.json"
    if not version_file.exists():
        return None
    try:
        return json.loads(version_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def read_event() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def emit_json(payload: dict) -> None:
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def model_context(hook_event_name: str, text: str) -> dict:
    """The hook output whose text Codex hands to the model.

    `hookSpecificOutput.additionalContext` is the only field that reaches the
    model on SessionStart, UserPromptSubmit, PreToolUse and PostToolUse. A
    top-level `systemMessage` is a UI warning and nothing more: Codex 0.155's
    hooks engine turns it into a `Warning` entry for the transcript and never
    adds it to the model's input (codex-rs/hooks/src/events/pre_tool_use.rs,
    `parse_completed`). `hookEventName` is required and must name the event --
    the wire structs deny unknown fields, so a wrong shape fails the hook run.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_event_name,
            "additionalContext": text,
        }
    }


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


def ix_healthy(cwd: str | Path | None) -> bool:
    if not ix_available():
        return False

    raw = _read_text(STATUS_CACHE_PATH)
    if raw is not None:
        try:
            cached = json.loads(raw)
        except json.JSONDecodeError:
            cached = None
        if isinstance(cached, dict):
            timestamp = _read_timestamp(cached.get("timestamp", 0))
            ok = bool(cached.get("ok", False))
            if time.time() - timestamp < HEALTH_TTL_SECONDS:
                return ok

    result = run_command(["ix", "status"], cwd=cwd, timeout=8)
    ok = bool(result and result.returncode == 0)
    _write_cache(STATUS_CACHE_PATH, {"timestamp": time.time(), "ok": ok})
    return ok


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


# A graph lookup (`locate`, `overview`, `impact`, ...) exits 2, 3 or 4 when ix
# answered with a reason rather than results (Ix #853): the record on stdout
# carries this slug under `error` (`code` for `locate`). Before #853 the same
# records came with exit 1, so the slug decides, not the exit code.
LOOKUP_OUTCOMES = {
    "ambiguous_target": "ambiguous",
    "unresolved_target": "not_found",
    "workspace_not_mapped": "not_mapped",
}
LOOKUP_ANSWER_EXITS = {1, 2, 3, 4}
# What one lookup answer may add to a hook's message.
MAX_LOOKUP_ITEMS = 5


def run_ix_lookup(
    argv: list[str], cwd: str | Path | None = None, timeout: int = 10
) -> tuple[str, dict | list | None]:
    """Run a graph lookup: `(outcome, payload)`.

    `ok` with the parsed stdout on exit 0; `ambiguous`, `not_found` or
    `not_mapped` with ix's record; `error` with None for everything else -- a
    real error, a timeout, stdout that is not one of those records. Unlike
    run_ix_json, a non-zero exit can be an answer, so this is for lookups only.
    """
    result = run_command(argv, cwd=cwd, timeout=timeout)
    if not result:
        return "error", None
    payload = parse_json_output(result.stdout or "")
    if result.returncode == 0:
        return "ok", payload
    if result.returncode in LOOKUP_ANSWER_EXITS and isinstance(payload, dict):
        slug = payload.get("error") or payload.get("code")
        outcome = LOOKUP_OUTCOMES.get(slug) if isinstance(slug, str) else None
        if outcome:
            return outcome, payload
    return "error", None


def _lookup_items(payload: dict, key: str) -> tuple[list, int]:
    items = payload.get(key)
    if not isinstance(items, list):
        return [], 0
    return items[:MAX_LOOKUP_ITEMS], max(0, len(items) - MAX_LOOKUP_ITEMS)


def _format_candidate(position: int, candidate: object) -> str:
    if not isinstance(candidate, dict):
        return f"[{position}] {candidate}"
    pick = candidate.get("pick")
    number = pick if isinstance(pick, int) and not isinstance(pick, bool) else position
    where = str(candidate.get("path") or "")
    line = candidate.get("line")
    if where and isinstance(line, int) and not isinstance(line, bool):
        where += f":{line}"
    details = ", ".join(part for part in (str(candidate.get("kind") or ""), where) if part)
    label = f"[{number}] {candidate.get('name') or '?'}"
    return f"{label} ({details})" if details else label


def _format_suggestion(suggestion: object) -> str:
    if not isinstance(suggestion, dict):
        return str(suggestion)
    name = str(suggestion.get("name") or "")
    path = str(suggestion.get("path") or "")
    if name and path:
        return f"{name} ({path})"
    return name or path


NOT_MAPPED_HINT = "workspace not mapped yet -- run `ix map <repo root>` to build the graph"


def describe_lookup(outcome: str, payload: dict | list | None, target: str) -> str:
    """One short line for a lookup ix answered without results, or "".

    Empty for `ok` and `error`, and for a not-found with no suggestions: the
    hooks enrich the model's own commands, and "not found" alone gives it
    nothing to act on -- most grep patterns are not symbol names.
    """
    if outcome == "not_mapped":
        return NOT_MAPPED_HINT
    if not isinstance(payload, dict):
        return ""
    if outcome == "ambiguous":
        candidates, more = _lookup_items(payload, "candidates")
        if not candidates:
            return ""
        listed = "; ".join(
            _format_candidate(position, candidate)
            for position, candidate in enumerate(candidates, start=1)
        )
        if more:
            listed += f" (+{more} more)"
        return f"'{target}' is ambiguous: {listed} -- retry with --pick <n> or --path <file>"
    if outcome == "not_found":
        suggestions, more = _lookup_items(payload, "suggestions")
        names = [name for name in map(_format_suggestion, suggestions) if name]
        if not names:
            return ""
        listed = ", ".join(names) + (f" (+{more} more)" if more else "")
        return f"'{target}' not found in the graph; did you mean: {listed}"
    return ""


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


def _is_pro_stub_response(result: subprocess.CompletedProcess[str]) -> bool:
    """True when ix answered with the Pro stub — a definitive 'not a Pro install'.

    The stub's contract is the literal string `The '<name>' command requires Ix
    Pro.` on exit 1. Matching it is what separates "Pro is absent" from "the
    probe happened to fail", which is the difference between a cacheable answer
    and one that must not be cached.
    """
    blob = f"{result.stdout or ''}{result.stderr or ''}"
    return "requires Ix Pro" in blob


def probe_pro(cwd: str | Path | None) -> tuple[bool, str | None]:
    """(Pro is available, the briefing this probe produced — if it ran one).

    The probe *is* `ix briefing`, so its output is the briefing. Returning it
    lets the one caller that wants a briefing use the one already paid for
    instead of running the most expensive command in the CLI a second time on
    the same prompt. The second element is None whenever there is nothing to
    reuse: answered from cache, not a Pro install, or no verdict.
    """
    raw = _read_text(PRO_CACHE_PATH)
    if raw is not None:
        try:
            cached = json.loads(raw)
        except json.JSONDecodeError:
            cached = None
        if isinstance(cached, dict):
            try:
                timestamp = float(cached.get("timestamp", 0))
            except (TypeError, ValueError):
                # A cache file with a junk timestamp is not a reason to take the
                # hook down; treat it as absent and re-probe. briefing_due()
                # below already handles its own cache this way.
                timestamp = 0.0
            ok = bool(cached.get("ok", False))
            # A tentative entry only suppresses re-probing; it never answers
            # "yes", so a Pro user cannot be locked out by one that says False.
            ttl = (
                PRO_PROBE_BACKOFF_SECONDS
                if cached.get("tentative")
                else PRO_TTL_SECONDS
            )
            if time.time() - timestamp < ttl:
                return ok, None

    # Probe with the real command, not `--help`.
    #
    # Pro commands are always *registered* — without @ix/pro the CLI installs a
    # stub for each one, whose action prints "The 'briefing' command requires Ix
    # Pro." and sets exit 1. But `--help` is handled by commander before any
    # action runs, so `ix briefing --help` exits 0 on a stub exactly as it does
    # on the real thing. Probing with it reported Pro as available on every OSS
    # install, and the hooks then ran Pro commands that could only fail.
    #
    # Running the command itself is the discriminator: the stub exits 1, the
    # real command exits 0. ix_healthy() is checked before this, so the backend
    # is already known reachable.
    result = run_command(["ix", "briefing", "--format", "json"], cwd=cwd, timeout=8)
    if result is None or (
        result.returncode != 0 and not _is_pro_stub_response(result)
    ):
        # No verdict. Either ix could not be run at all (timeout, OSError), or it
        # exited non-zero without the stub's sentinel — a backend hiccup, an
        # expired session, a slow first call. Answer False for this run, but do
        # not record it as the definitive "not Pro": PRO_TTL_SECONDS is an hour,
        # and one blip must not suppress Pro for a Pro user that long.
        #
        # It is still written, on the much shorter backoff TTL. Returning without
        # recording anything looks harmless and is not: the probe runs a real
        # `ix briefing`, so a backend slow enough to hit the 8s timeout would be
        # re-probed on every prompt, and the user would pay those 8 seconds every
        # time while Pro stayed off regardless.
        _write_cache(
            PRO_CACHE_PATH,
            {"timestamp": time.time(), "ok": False, "tentative": True},
        )
        return False, None
    ok = result.returncode == 0
    _write_cache(PRO_CACHE_PATH, {"timestamp": time.time(), "ok": ok})
    return ok, (_ix_text_from_stdout(result.stdout) if ok else None)


def ix_pro_available(cwd: str | Path | None) -> bool:
    return probe_pro(cwd)[0]


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


def looks_plain_pattern(pattern: str) -> bool:
    return bool(pattern) and not REGEX_META_RE.search(pattern)


def summarize_text_results(payload: dict | list | None) -> str:
    if isinstance(payload, dict):
        items = payload.get("results", [])
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    if not isinstance(items, list) or not items:
        return ""

    paths = [
        Path(str(item.get("path"))).name
        for item in items
        if isinstance(item, dict) and item.get("path")
    ]
    unique_paths: list[str] = []
    for path in paths:
        if path and path not in unique_paths:
            unique_paths.append(path)
        if len(unique_paths) == 4:
            break

    count = len(items)
    files = ", ".join(unique_paths)
    more = max(0, count - 4)
    summary = f"{count} text hits"
    if files:
        summary += f" in {files}"
    if more:
        summary += f" (+{more} more)"
    return summary


def summarize_locate_results(payload: dict | list | None) -> str:
    if not isinstance(payload, dict):
        return ""

    resolved = payload.get("resolvedTarget")
    if isinstance(resolved, dict) and resolved.get("name"):
        kind = str(resolved.get("kind") or "")
        path = Path(str(resolved.get("path") or "")).name
        suffix = ""
        if kind and path:
            suffix = f" ({kind}, {path})"
        elif kind:
            suffix = f" ({kind})"
        elif path:
            suffix = f" ({path})"
        return f"symbol: {resolved['name']}{suffix}"

    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        return ""

    names: list[str] = []
    for candidate in candidates[:3]:
        if not isinstance(candidate, dict) or not candidate.get("name"):
            continue
        label = candidate["name"]
        kind = str(candidate.get("kind") or "")
        if kind:
            label += f" ({kind})"
        names.append(label)
    if names:
        return "candidates: " + ", ".join(names)
    return ""


def summarize_inventory(payload: dict | list | None) -> str:
    if not isinstance(payload, dict):
        return ""
    summary = payload.get("summary", {})
    results = payload.get("results", [])
    total = 0
    if isinstance(summary, dict):
        total = int(summary.get("total", 0) or 0)
    if total == 0 and isinstance(results, list):
        total = len(results)
    if total == 0:
        return ""
    sample = []
    if isinstance(results, list):
        for item in results[:5]:
            if isinstance(item, dict) and item.get("name"):
                sample.append(str(item["name"]))
    text = f"{total} entities"
    if sample:
        text += ": " + ", ".join(sample)
        if total > len(sample):
            text += " ..."
    return text


def summarize_overview(payload: dict | list | None) -> str:
    if not isinstance(payload, dict):
        return ""
    key_items = payload.get("keyItems", [])
    names = [
        str(item.get("name"))
        for item in key_items[:5]
        if isinstance(item, dict) and item.get("name")
    ]
    children = payload.get("childrenByKind", {})
    parts = []
    if isinstance(children, dict):
        for kind, count in children.items():
            parts.append(f"{count} {kind}")
    if not names:
        return ""
    summary = "key: " + ", ".join(names)
    if parts:
        summary += " (" + ", ".join(parts) + ")"
    return summary


def summarize_impact(payload: dict | list | None) -> str:
    if not isinstance(payload, dict):
        return ""

    risk_level = str(payload.get("riskLevel") or "unknown").lower()
    summary = payload.get("summary", {})
    direct_dependents = 0
    member_level_callers = 0
    if isinstance(summary, dict):
        direct_dependents = int(summary.get("directDependents", 0) or 0)
        member_level_callers = int(summary.get("memberLevelCallers", 0) or 0)
    effective_dependents = max(direct_dependents, member_level_callers)

    if risk_level in {"unknown", "low"} or effective_dependents <= 2:
        return ""
    if risk_level == "critical":
        return f"CRITICAL: {effective_dependents} dependents"
    if risk_level == "high":
        return f"HIGH RISK: {effective_dependents} dependents"
    if risk_level == "medium":
        return f"{effective_dependents} dependents"
    return ""


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


def extract_search_pattern(command: str) -> str | None:
    if not command or any(operator in command for operator in SHELL_OPERATORS):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None

    if tokens[0] == "git" and len(tokens) > 1 and tokens[1] == "grep":
        tokens = tokens[1:]
    if tokens[0] not in SEARCH_COMMANDS:
        return None

    for index, token in enumerate(tokens[1:], start=1):
        if token in {"-e", "--regexp"} and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--regexp="):
            return token.split("=", 1)[1]

    skip_next = False
    for token in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in SEARCH_VALUE_OPTIONS:
            skip_next = True
            continue
        if any(token.startswith(prefix) for prefix in ("--glob=", "--max-count=", "--type=")):
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def extract_read_path(command: str) -> str | None:
    if not command or any(operator in command for operator in SHELL_OPERATORS):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or tokens[0] not in READ_COMMANDS:
        return None

    candidates = [token for token in tokens[1:] if not token.startswith("-")]
    if len(candidates) < 1:
        return None
    if tokens[0] == "awk" and len(candidates) >= 2:
        return candidates[-1]
    if tokens[0] == "sed" and len(candidates) >= 2:
        return candidates[-1]
    return candidates[-1]


def skip_read_path(file_path: str) -> bool:
    lowered = file_path.lower()
    if lowered.endswith(READ_SKIP_SUFFIXES):
        return True
    if any(segment in lowered for segment in READ_SKIP_SEGMENTS):
        return True
    if Path(lowered).name in READ_SKIP_BASENAMES:
        return True
    return False


def build_search_message(pattern: str, cwd: str | Path | None) -> str | None:
    if len(pattern) < 3:
        return None

    calls = [("text", run_ix_json, ["ix", "text", pattern, "--limit", "15", "--format", "json"], 10)]
    # Symbol lookup only for plain patterns: a regex is not a symbol name.
    # No `--limit` -- `ix locate` has no such option (it resolves one target and
    # lists the runners-up), and commander rejects the whole call over it.
    if looks_plain_pattern(pattern):
        calls.append(("locate", run_ix_lookup, ["ix", "locate", pattern, "--format", "json"], 10))
    results = run_parallel(calls, cwd)

    text_part = summarize_text_results(results.get("text"))
    locate_outcome, locate = results.get("locate") or ("error", None)
    if locate_outcome == "ok":
        locate_part = summarize_locate_results(locate)
    else:
        locate_part = describe_lookup(locate_outcome, locate, pattern)
    if not text_part and not locate_part:
        return None

    pieces = [f"[ix] bash grep intercepted for '{pattern}'"]
    if locate_part:
        pieces.append(locate_part)
    if text_part:
        pieces.append(text_part)
    pieces.append(f"Prefer: ix text '{pattern}' or ix locate '{pattern}' over shell grep")
    return " | ".join(pieces[:1] + pieces[1:])


def build_read_message(file_path: str, cwd: str | Path | None) -> str | None:
    if not file_path or skip_read_path(file_path):
        return None

    filename = Path(file_path).name
    if not filename:
        return None

    # Use a relative path for ix queries when possible so ix resolves the right
    # file instead of an arbitrary same-named file elsewhere in the repo.
    ix_query_target = filename
    if cwd and os.path.isabs(file_path):
        try:
            ix_query_target = str(Path(file_path).relative_to(Path(cwd).resolve()))
        except ValueError:
            pass

    results = run_parallel(
        [
            ("inventory", run_ix_json, ["ix", "inventory", "--kind", "file", "--path", filename, "--format", "json"], 10),
            ("overview", run_ix_lookup, ["ix", "overview", ix_query_target, "--format", "json"], 10),
            ("impact", run_ix_lookup, ["ix", "impact", ix_query_target, "--format", "json"], 10),
        ],
        cwd,
    )

    overview_outcome, overview = results.get("overview") or ("error", None)
    impact_outcome, impact = results.get("impact") or ("error", None)
    entity_part = summarize_overview(overview if overview_outcome == "ok" else None) or summarize_inventory(
        results.get("inventory")
    )
    risk_part = summarize_impact(impact if impact_outcome == "ok" else None)
    # Both lookups resolve the same target, so one answer of theirs is enough.
    lookup_parts = [
        part
        for part in (
            describe_lookup(overview_outcome, overview, ix_query_target),
            describe_lookup(impact_outcome, impact, ix_query_target),
        )
        if part
    ][:1]
    if not entity_part and not risk_part and not lookup_parts:
        return None

    pieces = [f"[ix] {filename}"]
    if entity_part:
        pieces.append(entity_part)
    if risk_part:
        pieces.append(risk_part)
    pieces.extend(lookup_parts)
    pieces.append("Use ix read <symbol> to get just a symbol's source")
    return " | ".join(pieces[:1] + pieces[1:])


def detect_file_write(command: str) -> list[str]:
    """Return file paths that will be written by this Bash command."""
    if not command:
        return []

    paths: list[str] = []

    for match in WRITE_REDIRECT_RE.finditer(command):
        path = match.group(1).strip("'\"")
        if (
            path
            and not path.startswith(("/dev/", "&", "-"))
            and not path.lower().endswith(WRITE_SKIP_SUFFIXES)
        ):
            paths.append(path)

    first_line = command.split("\n")[0]
    try:
        tokens = shlex.split(first_line)
    except ValueError:
        tokens = []

    if tokens:
        cmd = Path(tokens[0]).name
        if cmd == "tee":
            for tok in tokens[1:]:
                if not tok.startswith("-") and not tok.lower().endswith(WRITE_SKIP_SUFFIXES):
                    paths.append(tok)
        elif cmd in EDITOR_COMMANDS:
            for tok in tokens[1:]:
                if not tok.startswith("-") and not tok.lower().endswith(WRITE_SKIP_SUFFIXES):
                    paths.append(tok)

    return _unique(paths)


def _unique(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


# Codex edits files with its `apply_patch` tool, not with a shell command, and
# hands hooks the raw patch as `tool_input.command` (codex-rs/core/src/tools/
# handlers/apply_patch.rs, `pre_tool_use_payload`). These are the hunk headers of
# that format (codex-rs/apply-patch/src/parser.rs); Codex matches them against
# the trimmed line and takes the rest of it as the path, and so does this.
APPLY_PATCH_TOOL = "apply_patch"
_PATCH_FILE_MARKERS = (
    "*** Add File: ",
    "*** Delete File: ",
    "*** Update File: ",
    "*** Move to: ",
)


def parse_apply_patch_paths(patch: str) -> list[str]:
    """Every file an `apply_patch` input adds, deletes, updates or moves to."""
    if not patch:
        return []
    paths: list[str] = []
    for line in patch.splitlines():
        stripped = line.strip()
        for marker in _PATCH_FILE_MARKERS:
            if stripped.startswith(marker):
                path = stripped[len(marker):].strip()
                if path and not path.lower().endswith(WRITE_SKIP_SUFFIXES):
                    paths.append(path)
                break
    return _unique(paths)


def files_written(event: dict) -> list[str]:
    """The files this PreToolUse/PostToolUse event's tool call writes.

    `tool_name` is the canonical name Codex serialises -- `apply_patch` even when
    the hook was selected through its `Edit`/`Write` matcher aliases, and `Bash`
    for every shell tool (codex-rs/core/src/tools/hook_names.rs). Both put their
    input in `tool_input.command`.
    """
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    command = str(tool_input.get("command") or "")
    if event.get("tool_name") == APPLY_PATCH_TOOL:
        return parse_apply_patch_paths(command)
    return detect_file_write(command)


# One patch can touch many files. Each check is an `ix impact` with a 10 s
# timeout, so they run in parallel and only for the first few.
MAX_WRITE_WARNINGS = 3


def build_write_warnings(paths: list[str], cwd: str | Path | None) -> str | None:
    """`build_write_warning` for the first few `paths`, joined; None if all are safe."""
    targets = paths[:MAX_WRITE_WARNINGS]
    if not targets:
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as executor:
        warnings = list(executor.map(lambda path: build_write_warning(path, cwd), targets))
    # _unique: an unmapped workspace says so once, not once per file.
    found = _unique([warning for warning in warnings if warning])
    return "\n".join(found) if found else None


def build_write_warning(file_path: str, cwd: str | Path | None) -> str | None:
    """Run ix impact on a file before a write and return a warning, or None if safe."""
    filename = Path(file_path).name
    if not filename:
        return None

    outcome, impact = run_ix_lookup(["ix", "impact", file_path, "--format", "json"], cwd=cwd, timeout=10)
    if outcome == "not_mapped":
        return f"[ix] {NOT_MAPPED_HINT}"
    if outcome != "ok":
        # Not found with no suggestions is silent: a file this patch creates is
        # not in the graph yet.
        note = describe_lookup(outcome, impact, file_path)
        return f"[ix] {filename}: impact not checked, {note}" if note else None
    if not isinstance(impact, dict):
        return None

    risk_level = str(impact.get("riskLevel") or "unknown").lower()
    if risk_level in {"unknown", "low"}:
        return None

    summary = impact.get("summary", {})
    direct_deps = int(summary.get("directDependents", 0) or 0) if isinstance(summary, dict) else 0
    member_callers = int(summary.get("memberLevelCallers", 0) or 0) if isinstance(summary, dict) else 0
    effective_deps = max(direct_deps, member_callers)

    if effective_deps < 3:
        return None

    prefix = {
        "critical": "[ix] ⚠ CRITICAL EDIT",
        "high": "[ix] ⚠ HIGH-RISK EDIT",
        "medium": "[ix] NOTE",
    }.get(risk_level)

    if not prefix:
        return None

    return (
        f"{prefix} — {filename} has {effective_deps} dependents"
        f" | Run: ix impact '{file_path}' for full blast radius"
    )


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
