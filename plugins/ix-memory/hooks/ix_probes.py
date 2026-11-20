# Copyright 2026 Ix Infrastructure Inc.

"""Is `ix` reachable, and is it a Pro install: the cached health and Pro probes."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ix_proc import _ix_text_from_stdout, ix_available, run_command
from ix_state import PRO_CACHE_PATH, STATUS_CACHE_PATH, _read_text, _read_timestamp, _write_cache


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
