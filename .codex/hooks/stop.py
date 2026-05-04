#!/usr/bin/env python3
from __future__ import annotations

from common import (
    call_runtime,
    emit_json,
    find_workspace_root,
    git_revision,
    ix_healthy,
    read_event,
    spawn_background_ix_map,
)


def main() -> None:
    event = read_event()
    workspace_root = find_workspace_root(event.get("cwd"))
    if ix_healthy(workspace_root):
        rev = git_revision(workspace_root)
        payload: dict = {"trigger": "stop"}
        if rev:
            payload["revision"] = rev
        if call_runtime("/v2/ingest/map", payload, workspace_root=workspace_root) is None:
            spawn_background_ix_map(workspace_root)
    emit_json({"continue": True})


if __name__ == "__main__":
    main()
