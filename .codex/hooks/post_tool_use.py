#!/usr/bin/env python3
from __future__ import annotations

from common import (
    detect_file_write,
    find_workspace_root,
    ix_healthy,
    read_event,
    spawn_background_ix_ingest,
)


def main() -> None:
    event = read_event()
    workspace_root = find_workspace_root(event.get("cwd"))
    if not ix_healthy(workspace_root):
        return

    command = str(event.get("tool_input", {}).get("command") or "")
    if not command:
        return

    write_paths = detect_file_write(command)
    for path in write_paths[:3]:
        spawn_background_ix_ingest(path, workspace_root)


if __name__ == "__main__":
    main()
