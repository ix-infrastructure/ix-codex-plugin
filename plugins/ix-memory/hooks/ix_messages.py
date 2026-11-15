# Copyright 2026 Ix Infrastructure Inc.

"""The PreToolUse messages: graph context for a search or a read, and the pre-edit warning."""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path

from ix_lookup import describe_lookup, run_ix_lookup
from ix_proc import run_ix_json, run_parallel
from ix_shellparse import looks_plain_pattern, skip_read_path
from ix_summaries import (
    summarize_impact,
    summarize_inventory,
    summarize_locate_results,
    summarize_overview,
    summarize_text_results,
)


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
    # Ambiguity is left out: the model named an exact file, and the target ix
    # found ambiguous is the hook's own (often just the basename), so "retry
    # with --path" would be about a command the model never ran.
    lookup_parts = [
        part
        for part in (
            describe_lookup(outcome, payload, ix_query_target)
            for outcome, payload in ((overview_outcome, overview), (impact_outcome, impact))
            if outcome != "ambiguous"
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


# One patch can touch many files. Each check is an `ix impact` with a 10 s
# timeout, so they run in parallel and only for the first few.
MAX_WRITE_WARNINGS = 3


def build_write_warnings(
    paths: list[str], cwd: str | Path | None, session_cwd: str | Path | None = None
) -> str | None:
    """`build_write_warning` for the first few `paths`, joined; None if all are safe."""
    targets = paths[:MAX_WRITE_WARNINGS]
    if not targets:
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as executor:
        warnings = list(executor.map(lambda path: build_write_warning(path, cwd, session_cwd), targets))
    found = [warning for warning in warnings if warning]
    return "\n".join(found) if found else None


def _write_target_exists(file_path: str, base: str | Path | None) -> bool:
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(base or os.getcwd()) / path
    return path.exists()


def build_write_warning(
    file_path: str, cwd: str | Path | None, session_cwd: str | Path | None = None
) -> str | None:
    """Run ix impact on a file before a write and return a warning, or None if safe.

    `cwd` is where ix runs (the workspace root); `session_cwd` is where the
    command runs, which a relative `file_path` is relative to. It defaults to `cwd`.
    """
    filename = Path(file_path).name
    if not filename:
        return None

    outcome, impact = run_ix_lookup(["ix", "impact", file_path, "--format", "json"], cwd=cwd, timeout=10)
    if outcome == "not_found" and not _write_target_exists(file_path, session_cwd or cwd):
        # A file this patch creates is not in the graph yet; "did you mean"
        # would only point the model at a similarly named file.
        return None
    if outcome != "ok":
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
