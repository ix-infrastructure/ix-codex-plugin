#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

from __future__ import annotations

from common import emit_json, ix_available, read_event, request_auto_map


def main() -> None:
    event = read_event()
    # Every guard lives in request_auto_map: a git repository that is not $HOME,
    # already mapped, and not refreshed in the last few minutes. The map itself
    # is detached, so this hook costs at most one `git rev-parse` and one
    # `ix status`, both bounded well inside the hook's 10 s timeout.
    if ix_available():
        request_auto_map(event.get("cwd"))
    emit_json({"continue": True})


if __name__ == "__main__":
    main()
