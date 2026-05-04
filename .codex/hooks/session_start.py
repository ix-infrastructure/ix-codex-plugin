#!/usr/bin/env python3
from __future__ import annotations

from common import (
    call_runtime,
    emit_json,
    find_workspace_root,
    format_status_briefing,
    get_runtime,
    ix_healthy,
    load_plugin_version,
    read_event,
)

_STATIC_GUIDANCE = "\n".join([
    "Ix Memory is available in this workspace.",
    "Use these cognitive skills to reason about the codebase.",
    "IMPORTANT: invoke skills by name as Codex skills — do NOT run them as shell commands.",
    "- ix-understand [target] — build a mental model of a system or the whole repo",
    "- ix-investigate <symbol> — deep dive into a component",
    "- ix-impact <target> — change-risk analysis before non-trivial edits",
    "- ix-plan <targets...> — risk-ordered plan for multi-file work",
    "- ix-debug <symptom> — root-cause analysis from a symptom",
    "- ix-architecture [scope] — design health and coupling analysis",
    "- ix-docs <target> — narrative-first documentation",
    "",
    "Behavioral rules:",
    "- Graph before code.",
    "- Read at symbol level only with `ix read <symbol>`.",
    "- Stop early once the question is answered.",
    "- Label graph-backed facts separately from inferences.",
])


def _format_version_line(meta: dict | None) -> str | None:
    if not meta:
        return None
    name = meta.get("plugin_name", "ix-memory")
    version = meta.get("plugin_version", "?")
    installed_at = str(meta.get("installed_at", ""))
    commit = str(meta.get("git_commit", ""))
    parts = [f"{name} plugin v{version} active"]
    if installed_at:
        parts.append(f"installed {installed_at[:10]}")
    if commit:
        parts.append(f"commit {commit[:7]}")
    return " | ".join(parts)


def main() -> None:
    event = read_event()
    workspace_root = find_workspace_root(event.get("cwd"))
    if not ix_healthy(workspace_root):
        return

    context = None

    # Try runtime API: health probe then dynamic briefing
    health = get_runtime("/v2/status")
    if health is not None:
        response = call_runtime(
            "/v2/ix_query", {"mode": "status"}, workspace_root=workspace_root
        )
        context = format_status_briefing(response)

    if context is None:
        context = _STATIC_GUIDANCE

    version_line = _format_version_line(load_plugin_version())
    if version_line:
        context = version_line + "\n\n" + context

    emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }
    )


if __name__ == "__main__":
    main()
