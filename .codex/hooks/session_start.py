#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

from __future__ import annotations

from common import (
    emit_json,
    find_workspace_root,
    ix_healthy,
    load_plugin_version,
    model_context,
    read_event,
)

_STATIC_GUIDANCE = "\n".join([
    "Ix Memory is available in this workspace.",
    "Before rg/grep for symbols, run `ix locate <symbol>` or `ix search <term>`.",
    "Run `ix callers <symbol>` and `ix impact <symbol>` before editing a symbol.",
    "Ambiguous? Retry with `--pick N`, `--path <path>`, or `--kind <kind>`.",
    "Not found means a new or misspelled symbol: search a partial name, then rg for symbols you just wrote.",
    "Use $ix-* skills in Codex, never as shell commands. Read symbols with `ix read <symbol>`. "
    "Stop when answered; label graph facts vs inferences.",
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
    if workspace_root is None or not ix_healthy(workspace_root):
        return

    context = _STATIC_GUIDANCE

    version_line = _format_version_line(load_plugin_version())
    if version_line:
        context = version_line + "\n\n" + context

    emit_json(model_context("SessionStart", context))


if __name__ == "__main__":
    main()
