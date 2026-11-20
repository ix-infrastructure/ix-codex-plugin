# Copyright 2026 Ix Infrastructure Inc.

"""Codex hook I/O: the event on stdin, the JSON reply on stdout, the version file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ix_once import first_delivery


def load_plugin_version() -> dict | None:
    """The version this copy of the hooks belongs to.

    A `--hooks` install has the installer's `.codex/ix-plugin-version.json` one
    level up. The plugin's own copy (run by Codex from its install of the plugin,
    or a symlink install resolved to the checkout) has the plugin manifest there.
    """
    root = Path(__file__).parent.parent
    version_file = root / "ix-plugin-version.json"
    if version_file.exists():
        try:
            return json.loads(version_file.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    try:
        manifest = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(manifest, dict) or not manifest.get("version"):
        return None
    return {"plugin_name": manifest.get("name", "ix-memory"), "plugin_version": manifest["version"]}


def read_event() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    # Only an event Codex sent: it always names the session and the event.
    if payload.get("session_id") and payload.get("hook_event_name") and not first_delivery(raw):
        raise SystemExit(0)  # another copy of these hooks is handling it
    return payload


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
