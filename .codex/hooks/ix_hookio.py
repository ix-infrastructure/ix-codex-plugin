# Copyright 2026 Ix Infrastructure Inc.

"""Codex hook I/O: the event on stdin, the JSON reply on stdout, the version file."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_plugin_version() -> dict | None:
    """Load the version metadata written by the installer into .codex/ix-plugin-version.json."""
    version_file = Path(__file__).parent.parent / "ix-plugin-version.json"
    if not version_file.exists():
        return None
    try:
        return json.loads(version_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def read_event() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def emit_json(payload: dict) -> None:
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def model_context(hook_event_name: str, text: str) -> dict:
    """The hook output whose text Codex hands to the model.

    `hookSpecificOutput.additionalContext` is the only field that reaches the
    model on SessionStart, UserPromptSubmit, PreToolUse and PostToolUse. A
    top-level `systemMessage` is a UI warning and nothing more: Codex 0.155's
    hooks engine turns it into a `Warning` entry for the transcript and never
    adds it to the model's input (codex-rs/hooks/src/events/pre_tool_use.rs,
    `parse_completed`). `hookEventName` is required and must name the event --
    the wire structs deny unknown fields, so a wrong shape fails the hook run.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_event_name,
            "additionalContext": text,
        }
    }
