#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

from __future__ import annotations

from common import (
    APPLY_PATCH_TOOL,
    build_read_message,
    build_search_message,
    build_write_warnings,
    emit_json,
    extract_read_path,
    extract_search_pattern,
    files_written,
    find_workspace_root,
    ix_healthy,
    model_context,
    read_event,
)


def main() -> None:
    event = read_event()
    workspace_root = find_workspace_root(event.get("cwd"))
    if workspace_root is None or not ix_healthy(workspace_root):
        return

    tool_input = event.get("tool_input")
    command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
    if not command:
        return

    message = None

    write_paths = files_written(event)
    if write_paths:
        message = build_write_warnings(write_paths, workspace_root)
    elif event.get("tool_name") != APPLY_PATCH_TOOL:
        # Search and read interception are about shell commands; a patch that
        # names no file has nothing for them to look at.
        pattern = extract_search_pattern(command)
        if pattern:
            message = build_search_message(pattern, workspace_root)
        else:
            file_path = extract_read_path(command)
            if file_path:
                message = build_read_message(file_path, workspace_root)

    if not message:
        return

    # additionalContext, not systemMessage: Codex shows a systemMessage to the
    # user as a warning and never gives it to the model, which is who this is for.
    emit_json(model_context("PreToolUse", message))


if __name__ == "__main__":
    main()
