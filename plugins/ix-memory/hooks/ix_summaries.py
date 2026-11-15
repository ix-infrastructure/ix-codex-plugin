# Copyright 2026 Ix Infrastructure Inc.

"""One-line summaries of `ix text`, `locate`, `inventory`, `overview` and `impact` output."""

from __future__ import annotations

from pathlib import Path


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
