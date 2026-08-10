#!/usr/bin/env python3
from __future__ import annotations

from common import (
    briefing_due,
    call_runtime,
    emit_json,
    find_workspace_root,
    format_status_briefing,
    ix_healthy,
    mark_briefing_sent,
    probe_pro,
    read_event,
    run_ix_text,
)


def main() -> None:
    event = read_event()
    workspace_root = find_workspace_root(event.get("cwd"))
    if not ix_healthy(workspace_root):
        return
    # briefing_due() before ix_pro_available(), not after: the first is a local
    # file read and the second now runs a real `ix briefing`. In the other order
    # the costliest command in the CLI runs on the UserPromptSubmit critical path
    # and is then thrown away, because the very next line decides there is
    # nothing to say. Harmless when the probe was `ix briefing --help`; not now.
    if not briefing_due():
        return
    # The Pro probe runs `ix briefing` — so when it had to run one, keep it.
    pro, probed_briefing = probe_pro(workspace_root)
    if not pro:
        return

    # Try runtime API first
    response = call_runtime(
        "/v2/ix_query", {"mode": "status"}, workspace_root=workspace_root
    )
    briefing = format_status_briefing(response)

    if briefing is None:
        # Fall back to the ix briefing CLI — reusing the probe's output when it
        # produced one, rather than running the same command twice on one prompt.
        # probed_briefing is None whenever the probe answered from cache, which
        # is the common case; then this really does have to run.
        briefing = probed_briefing or run_ix_text(
            ["ix", "briefing", "--format", "json"], cwd=workspace_root, timeout=8
        )

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
