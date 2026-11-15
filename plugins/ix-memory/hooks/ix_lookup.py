# Copyright 2026 Ix Infrastructure Inc.

"""Graph lookups (`locate`, `overview`, `impact`): ix's answer records, and one line about them."""

from __future__ import annotations

from pathlib import Path

from ix_proc import parse_json_output, run_command


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


def _pick_number(candidate: object) -> int | None:
    """ix's own `--pick` number for a candidate, or None.

    Never the list position: how ix numbers `--pick` is ix's to say, and a
    guessed number would send the model's retry to the wrong entity.
    """
    pick = candidate.get("pick") if isinstance(candidate, dict) else None
    return pick if isinstance(pick, int) and not isinstance(pick, bool) else None


def _format_candidate(candidate: object) -> str:
    if not isinstance(candidate, dict):
        return str(candidate)
    where = str(candidate.get("path") or "")
    line = candidate.get("line")
    if where and isinstance(line, int) and not isinstance(line, bool):
        where += f":{line}"
    details = ", ".join(part for part in (str(candidate.get("kind") or ""), where) if part)
    number = _pick_number(candidate)
    label = str(candidate.get("name") or "?")
    if number is not None:
        label = f"[{number}] {label}"
    return f"{label} ({details})" if details else label


def _format_suggestion(suggestion: object) -> str:
    if not isinstance(suggestion, dict):
        return str(suggestion)
    name = str(suggestion.get("name") or "")
    path = str(suggestion.get("path") or "")
    if name and path:
        return f"{name} ({path})"
    return name or path


def describe_lookup(outcome: str, payload: dict | list | None, target: str) -> str:
    """One short line for a lookup ix answered without results, or "".

    Empty for `ok` and `error`, and for a not-found with no suggestions: the
    hooks enrich the model's own commands, and "not found" alone gives it
    nothing to act on -- most grep patterns are not symbol names. Empty for
    `not_mapped` too: the hooks run on every grep, read and write, and mapping
    is the user's call (an automatic map never creates a workspace).
    """
    if not isinstance(payload, dict):
        return ""
    if outcome == "ambiguous":
        candidates, more = _lookup_items(payload, "candidates")
        if not candidates:
            return ""
        listed = "; ".join(map(_format_candidate, candidates))
        if more:
            listed += f" (+{more} more)"
        if any(_pick_number(candidate) is not None for candidate in candidates):
            retry = "--pick <n> or --path <file>"
        else:
            retry = "--path <file>"
        return f"'{target}' is ambiguous: {listed} -- retry with {retry}"
    if outcome == "not_found":
        suggestions, more = _lookup_items(payload, "suggestions")
        names = [name for name in map(_format_suggestion, suggestions) if name]
        if not names:
            return ""
        listed = ", ".join(names) + (f" (+{more} more)" if more else "")
        return f"'{target}' not found in the graph; did you mean: {listed}"
    return ""
