#!/usr/bin/env python3
from __future__ import annotations

from common import (
    briefing_due,
    emit_json,
    find_workspace_root,
    ix_healthy,
    ix_pro_available,
    mark_briefing_sent,
    read_event,
    run_ix_text,
)


def main() -> None:
    event = read_event()
    workspace_root = find_workspace_root(event.get("cwd"))
    if not ix_healthy(workspace_root) or not ix_pro_available(workspace_root):
        return
    if not briefing_due():
        return

    briefing = run_ix_text(["ix", "briefing", "--format", "json"], cwd=workspace_root, timeout=8)
    if not briefing:
        return

    mark_briefing_sent()
    emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "[ix] Session briefing:\n" + briefing,
            }
        }
    )


if __name__ == "__main__":
    main()
