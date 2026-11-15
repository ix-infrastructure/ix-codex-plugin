# Copyright 2026 Ix Infrastructure Inc.

"""Reading Codex's `apply_patch` input, and the files any tool call writes."""

from __future__ import annotations

from ix_shellparse import WRITE_SKIP_SUFFIXES, _unique, detect_file_write


# Codex edits files with its `apply_patch` tool, not with a shell command, and
# hands hooks the raw patch as `tool_input.command` (codex-rs/core/src/tools/
# handlers/apply_patch.rs, `pre_tool_use_payload`). These are the hunk headers of
# that format (codex-rs/apply-patch/src/parser.rs); Codex matches them against
# the trimmed line and takes the rest of it as the path, and so does this.
APPLY_PATCH_TOOL = "apply_patch"
_PATCH_FILE_MARKERS = (
    "*** Add File: ",
    "*** Delete File: ",
    "*** Update File: ",
    "*** Move to: ",
)


def parse_apply_patch_paths(patch: str) -> list[str]:
    """Every file an `apply_patch` input adds, deletes, updates or moves to."""
    if not patch:
        return []
    paths: list[str] = []
    for line in patch.splitlines():
        stripped = line.strip()
        for marker in _PATCH_FILE_MARKERS:
            if stripped.startswith(marker):
                path = stripped[len(marker):].strip()
                if path and not path.lower().endswith(WRITE_SKIP_SUFFIXES):
                    paths.append(path)
                break
    return _unique(paths)


def files_written(event: dict) -> list[str]:
    """The files this PreToolUse/PostToolUse event's tool call writes.

    `tool_name` is the canonical name Codex serialises -- `apply_patch` even when
    the hook was selected through its `Edit`/`Write` matcher aliases, and `Bash`
    for every shell tool (codex-rs/core/src/tools/hook_names.rs). Both put their
    input in `tool_input.command`.
    """
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    command = str(tool_input.get("command") or "")
    if event.get("tool_name") == APPLY_PATCH_TOOL:
        return parse_apply_patch_paths(command)
    return detect_file_write(command)
