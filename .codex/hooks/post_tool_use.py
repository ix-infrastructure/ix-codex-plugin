#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

from __future__ import annotations

from common import detect_file_write, ix_available, read_event, request_auto_map


def main() -> None:
    event = read_event()
    command = str(event.get("tool_input", {}).get("command") or "")
    if not command or not detect_file_write(command):
        return
    # Not `ix map <file>`: map takes a directory and rejects a file outright.
    # A write asks for the same guarded, debounced repository refresh the Stop
    # hook does, and the CLI's incremental map picks up what changed.
    if ix_available():
        request_auto_map(event.get("cwd"))


if __name__ == "__main__":
    main()
