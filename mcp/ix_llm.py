"""ix `--format llm` fast-path, gated on the installed CLI's version.

Every read tool in `server.py` calls `_json()` and hands the result to `_ok()`,
which re-serialises it with `indent=2`. So the JSON is parsed only to be printed
straight back to the model — and printed *larger* than it arrived. That is
exactly the overhead `--format llm` exists to remove: a token-minimal,
newline-delimited rendering, typically 2-4x smaller than `json` on tree- and
table-shaped output.

The port target is ix-cursor-plugin's `mcp/lib/llm.ts`, which has run this
design since v0.7.0. Two things had to change for this surface.

## Why the floor is per-command, not global

`--format llm` did not arrive all at once, and the cursor plugin's single
`MIN_LLM_VERSION = 0.7.0` is correct only because none of its twelve tools
touches a command that landed later. This server exposes two that do.

| commands | renderer landed in |
|---|---|
| Tier 1-4 — `map` `subsystems` `impact` `smells` `overview` `stats` `inventory` `rank` `depends` `trace` `callers` `callees` `imports` `imported-by` `text` `history` `locate` `diff` | **v0.7.0** |
| Tier 5 — `explain` `read` | **v0.9.2** |

## Why a wrong floor fails silently

`ix` does not validate `--format`. Every renderer is
`if json: ... elif llm: ... else: text`, so an unrecognised value falls through
to **human text** and exits 0 (verified against `formatPatches` and its
siblings). An old CLI therefore answers `--format llm` with a rendered table
rather than an error — no exception to catch, no non-zero exit, just prose where
records were expected. That is the failure this table prevents, and it is why
the floors are stated per command instead of being probed.

The same property is what makes the whole change safe: there is no version of
`ix` on which asking for `llm` breaks.

## Pro commands are excluded outright

`briefing` and `decisions` come from `@ix/pro`, whose commands declare only
`text|json` — there is no `llm` renderer at any version. They are absent from
the table below and must stay absent: a version gate cannot help, because no
future release of the *OSS* CLI adds them.

## Invariants

Every fall-through returns None so the caller runs its unchanged JSON path,
which keeps behaviour byte-identical to before this module existed:

  - the command is not in the table, or the CLI is older than its floor
  - the CLI version cannot be determined
  - any error, timeout, or empty output
  - `IX_DISABLE_LLM_FORMAT=1` (kill switch)

This module never *parses* llm output. It forwards it.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Optional

# command -> (major, minor, patch) of the release whose renderer it needs.
# Derived from docs/llm-format.md in the Ix repo and confirmed against the tags
# that contain each tier's commit.
LLM_MIN_VERSION: dict[str, tuple[int, int, int]] = {
    # Tier 1
    "map": (0, 7, 0),
    "subsystems": (0, 7, 0),
    "impact": (0, 7, 0),
    "smells": (0, 7, 0),
    "overview": (0, 7, 0),
    "stats": (0, 7, 0),
    # Tier 2
    "inventory": (0, 7, 0),
    "rank": (0, 7, 0),
    "depends": (0, 7, 0),
    "trace": (0, 7, 0),
    "callers": (0, 7, 0),
    "callees": (0, 7, 0),
    "imports": (0, 7, 0),
    "imported-by": (0, 7, 0),
    # Tier 3
    "text": (0, 7, 0),
    "history": (0, 7, 0),
    # Tier 4
    "locate": (0, 7, 0),
    "diff": (0, 7, 0),
    # Tier 5 — these are the reason the table is per-command.
    "explain": (0, 9, 2),
    "read": (0, 9, 2),
}

SemVer = tuple[int, int, int]

_SEMVER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# Process-lifetime memo. A server probes the CLI version at most once; the
# probe is deliberately not cached to disk so the gate stays deterministic and
# resettable in tests.
_version_cache: Optional[SemVer] = None
_version_probed = False


def reset_version_cache() -> None:
    """Forget the probed version. For tests."""
    global _version_cache, _version_probed
    _version_cache = None
    _version_probed = False


def parse_semver(value: str) -> Optional[SemVer]:
    match = _SEMVER_RE.search(value or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def llm_disabled() -> bool:
    return (os.environ.get("IX_DISABLE_LLM_FORMAT") or "").strip().lower() in {"1", "true", "yes"}


def detect_version(run: Callable[..., tuple[bool, str, str]]) -> Optional[SemVer]:
    """Probe `ix --version`, memoised for the life of the process.

    A generous timeout on purpose: `ix` kicks off a non-awaited update check
    that delays process exit, and the probe resolves on exit. A tight timeout
    would fail the probe intermittently and silently disable the fast-path for
    the rest of the session. Failing to determine a version disables it too, so
    the cost of being wrong here is only lost savings, never wrong output.
    """
    global _version_cache, _version_probed
    if _version_probed:
        return _version_cache

    _version_probed = True
    try:
        ok, stdout, _ = run(["--version"], timeout=20)
    except Exception:
        _version_cache = None
        return None

    _version_cache = parse_semver(stdout.strip()) if ok else None
    return _version_cache


# Flag combinations that route to text even on a current CLI, documented as
# deliberate exceptions in docs/llm-format.md: `diff --content` emits verbatim
# hunks, which have no record form. `ix_diff` does not pass `--content` today,
# so this guards a future edit rather than a live case — but the failure it
# prevents is the silent kind, text forwarded as though it were records.
_TEXT_ONLY_FLAGS: dict[str, frozenset[str]] = {
    "diff": frozenset({"--content"}),
}


def supports_llm(command: str, run: Callable[..., tuple[bool, str, str]], args: Optional[list[str]] = None) -> bool:
    if llm_disabled():
        return False
    floor = LLM_MIN_VERSION.get(command)
    if floor is None:
        return False
    if args and (blocked := _TEXT_ONLY_FLAGS.get(command)):
        if blocked.intersection(args):
            return False
    version = detect_version(run)
    return version is not None and version >= floor


def is_llm_error_line(text: str) -> bool:
    """`ix` reports some failures as a record on stdout, with exit 0.

    `error code=<slug> message="..."` is part of the llm format by design. A
    caller that only checked the exit status would forward that line to the
    model as a successful result, so it is detected here and deferred to the
    JSON path, where the error is formatted exactly as it always was. No success
    record for any command in the table begins with `error code=`.
    """
    return text.lstrip().startswith("error code=")


def try_llm(
    args: list[str],
    run: Callable[..., tuple[bool, str, str]],
    timeout: int = 15,
) -> Optional[str]:
    """Return `--format llm` text for `args`, or None to use the JSON path.

    `args[0]` is the ix subcommand; the gate is keyed on it.
    """
    if not args:
        return None
    if not supports_llm(args[0], run, args):
        return None

    try:
        ok, stdout, _ = run([*args, "--format", "llm"], timeout=timeout)
    except Exception:
        return None
    if not ok:
        return None

    text = (stdout or "").strip()
    if not text:
        return None
    if is_llm_error_line(text):
        return None
    return text
