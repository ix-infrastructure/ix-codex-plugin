# Copyright 2026 Ix Infrastructure Inc.

"""Reading the model's shell commands: the search pattern, the file read, the files written."""

from __future__ import annotations

import re
import shlex
from pathlib import Path


SHELL_OPERATORS = ("|", "&&", "||", ";", "$(", "`")

# Output redirect: > or >> not preceded by 2 (stderr), < (heredoc), or > (already matched)
WRITE_REDIRECT_RE = re.compile(r"(?<![2<>])>>?\s+([^\s|;&<>]+)")
EDITOR_COMMANDS = frozenset({"vim", "vi", "nvim", "nano", "emacs", "hx", "micro"})
WRITE_SKIP_SUFFIXES = (
    ".bin", ".exe", ".gif", ".gz", ".ico", ".jpeg", ".jpg",
    ".pdf", ".png", ".tar", ".zip", ".lock", ".sum",
)
SEARCH_COMMANDS = {"grep", "rg"}
READ_COMMANDS = {"cat", "head", "tail", "sed", "awk"}
REGEX_META_RE = re.compile(r"[\\^$\[\](){}|*+?]")
SEARCH_VALUE_OPTIONS = {
    "-A",
    "-B",
    "-C",
    "-e",
    "-f",
    "-g",
    "-m",
    "-t",
    "--context",
    "--file",
    "--glob",
    "--max-count",
    "--regexp",
    "--type",
    "--type-add",
}
READ_SKIP_SUFFIXES = (
    ".bin",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".tar",
    ".zip",
)
READ_SKIP_SEGMENTS = (
    "/.git/",
    "/__pycache__/",
    "/build/",
    "/dist/",
    "/generated/",
    "/node_modules/",
)
READ_SKIP_BASENAMES = {
    "cargo.lock",
    "go.sum",
    "package-lock.json",
    "pnpm-lock.yaml",
    "skill.md",
    "yarn.lock",
}


def looks_plain_pattern(pattern: str) -> bool:
    return bool(pattern) and not REGEX_META_RE.search(pattern)


def extract_search_pattern(command: str) -> str | None:
    if not command or any(operator in command for operator in SHELL_OPERATORS):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None

    if tokens[0] == "git" and len(tokens) > 1 and tokens[1] == "grep":
        tokens = tokens[1:]
    if tokens[0] not in SEARCH_COMMANDS:
        return None

    for index, token in enumerate(tokens[1:], start=1):
        if token in {"-e", "--regexp"} and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--regexp="):
            return token.split("=", 1)[1]

    skip_next = False
    for token in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in SEARCH_VALUE_OPTIONS:
            skip_next = True
            continue
        if any(token.startswith(prefix) for prefix in ("--glob=", "--max-count=", "--type=")):
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def extract_read_path(command: str) -> str | None:
    if not command or any(operator in command for operator in SHELL_OPERATORS):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or tokens[0] not in READ_COMMANDS:
        return None

    candidates = [token for token in tokens[1:] if not token.startswith("-")]
    if len(candidates) < 1:
        return None
    if tokens[0] == "awk" and len(candidates) >= 2:
        return candidates[-1]
    if tokens[0] == "sed" and len(candidates) >= 2:
        return candidates[-1]
    return candidates[-1]


def skip_read_path(file_path: str) -> bool:
    lowered = file_path.lower()
    if lowered.endswith(READ_SKIP_SUFFIXES):
        return True
    if any(segment in lowered for segment in READ_SKIP_SEGMENTS):
        return True
    if Path(lowered).name in READ_SKIP_BASENAMES:
        return True
    return False


def detect_file_write(command: str) -> list[str]:
    """Return file paths that will be written by this Bash command."""
    if not command:
        return []

    paths: list[str] = []

    for match in WRITE_REDIRECT_RE.finditer(command):
        path = match.group(1).strip("'\"")
        if (
            path
            and not path.startswith(("/dev/", "&", "-"))
            and not path.lower().endswith(WRITE_SKIP_SUFFIXES)
        ):
            paths.append(path)

    first_line = command.split("\n")[0]
    try:
        tokens = shlex.split(first_line)
    except ValueError:
        tokens = []

    if tokens:
        cmd = Path(tokens[0]).name
        if cmd == "tee":
            for tok in tokens[1:]:
                if not tok.startswith("-") and not tok.lower().endswith(WRITE_SKIP_SUFFIXES):
                    paths.append(tok)
        elif cmd in EDITOR_COMMANDS:
            for tok in tokens[1:]:
                if not tok.startswith("-") and not tok.lower().endswith(WRITE_SKIP_SUFFIXES):
                    paths.append(tok)

    return _unique(paths)


def _unique(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result
