#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

from __future__ import annotations

from common import files_written, ix_available, read_event, request_auto_map


def main() -> None:
    event = read_event()
    # A shell redirect or an `apply_patch` -- the tool Codex edits files with.
    if not files_written(event):
        return
    # Not `ix map <file>`: map takes a directory and rejects a file outright.
    # A write asks for the same guarded, debounced repository refresh the Stop
    # hook does, and the CLI's incremental map picks up what changed.
    if ix_available():
        request_auto_map(event.get("cwd"))


if __name__ == "__main__":
    main()
