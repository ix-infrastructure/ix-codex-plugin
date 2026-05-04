#!/usr/bin/env python3
from __future__ import annotations

from common import (
    build_read_message,
    build_search_message,
    build_write_warning,
    detect_file_write,
    emit_json,
    extract_read_path,
    extract_search_pattern,
    find_workspace_root,
    ix_healthy,
    read_event,
)


def main() -> None:
    event = read_event()
    workspace_root = find_workspace_root(event.get("cwd"))
    if not ix_healthy(workspace_root):
        return

    command = str(event.get("tool_input", {}).get("command") or "")
    if not command:
        return

    message = None

    write_paths = detect_file_write(command)
    if write_paths:
        message = build_write_warning(write_paths[0], workspace_root)
    else:
        pattern = extract_search_pattern(command)
        if pattern:
            message = build_search_message(pattern, workspace_root)
        else:
            file_path = extract_read_path(command)
            if file_path:
                message = build_read_message(file_path, workspace_root)

    if not message:
        return

    emit_json({"systemMessage": message})


if __name__ == "__main__":
    main()
