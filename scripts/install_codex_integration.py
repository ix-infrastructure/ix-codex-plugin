#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""Install the ix-memory plugin, its Codex hooks and its MCP registration.

Re-running this is how users upgrade, and the hosted one-liners never pass
`--force`, so a re-run over any earlier version has to just work:

* files the plugin owns are overwritten, and ones it no longer ships are
  removed. Ownership is recorded in `.codex/ix-plugin-version.json` (`files`);
  installs that predate the record are recognised by the file names every
  earlier version shipped;
* `hooks.json` is merged, not copied: the plugin's own entries -- from any
  earlier version, whatever their command string or matcher -- are replaced in
  place, and every other hook in the file is kept;
* nothing is written until everything has been read and checked, and the writes
  themselves are staged beside their destinations and swapped in together, with
  the previous files restored if any swap fails. An install either lands whole
  or leaves the target as it found it.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        # Only read: for a hooks-disabled warning, to recognise a stale MCP
        # registration, and to prove a rewritten config still parses. Each of
        # those is skipped, never guessed at, without a parser.
        tomllib = None  # type: ignore[assignment]


PLUGIN_NAME = "ix-memory"
DEFAULT_MARKETPLACE_NAME = "ix-codex-plugin"
DEFAULT_MARKETPLACE_DISPLAY_NAME = "ix-codex-plugin"
PLUGIN_ENTRY = {
    "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    },
    "category": "Productivity",
}

VERSION_FILE = Path(".codex") / "ix-plugin-version.json"

# Every file an install made before the version file recorded `files`. 2.4.1 and
# earlier also copied an MCP server into `.codex/mcp/`; those two are handled by
# remove_legacy_mcp_files, after the registration that may still point at them.
LEGACY_HOOK_FILES = tuple(
    f".codex/hooks/{name}.py"
    for name in (
        "_launch",
        "common",
        "post_tool_use",
        "pre_tool_use",
        "session_start",
        "stop",
        "user_prompt_submit",
    )
)
LEGACY_MCP_FILES = (".codex/mcp/server.py", ".codex/mcp/ix_llm.py")


class InstallError(Exception):
    """A problem found before, or while, writing. Reported without a traceback."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install or upgrade the ix-codex-plugin package, hooks, and MCP registration "
            "in a repo or home config. Re-running it upgrades in place."
        )
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--repo", help="Target repository root")
    target_group.add_argument(
        "--home",
        action="store_true",
        help="Install into the current user's home Codex config",
    )
    parser.add_argument(
        "--plugin",
        action="store_true",
        help="Copy/register the ix-memory plugin into a marketplace-backed location",
    )
    parser.add_argument(
        "--hooks",
        action="store_true",
        help="Install the repo/home .codex hook bundle",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Register the Ix CLI's MCP server (`ix mcp`) with Codex",
    )
    parser.add_argument(
        "--mode",
        choices=("copy", "symlink"),
        default="copy",
        help="How to install files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Also replace what is not the plugin's: a marketplace entry named ix-memory "
            "that points elsewhere, an unreadable hooks.json, and an ix-memory MCP "
            "server that is not `ix mcp`. Not needed to upgrade."
        ),
    )
    parser.add_argument(
        "--marketplace-name",
        default=DEFAULT_MARKETPLACE_NAME,
        help="Marketplace name to create if the target has no marketplace yet",
    )
    parser.add_argument(
        "--marketplace-display-name",
        default=DEFAULT_MARKETPLACE_DISPLAY_NAME,
        help="Marketplace display name to create if the target has no marketplace yet",
    )
    args = parser.parse_args(argv)
    if not args.plugin and not args.hooks and not args.mcp:
        args.plugin = True
        args.hooks = True
        args.mcp = True
    return args


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def source_codex_dir() -> Path:
    return repo_root() / ".codex"


def source_plugin_dir() -> Path:
    return repo_root() / "plugins" / PLUGIN_NAME


def target_root_from_args(args: argparse.Namespace) -> Path:
    if args.home:
        return Path.home().resolve()
    return Path(args.repo).expanduser().resolve()


def same_link(destination: Path, source: Path) -> bool:
    return destination.is_symlink() and destination.resolve() == source.resolve()


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:  # pragma: no cover - unresolvable path
        return False


def _same_location(left: Path, right: Path) -> bool:
    """True when both name one directory entry, as installing a checkout into itself does.

    The parents are resolved, the entry itself is not: a symlink an earlier
    `--mode symlink` install left at the destination points at the source, but
    is not the source, and has to be replaceable.
    """
    return left.name == right.name and _same_path(left.parent, right.parent)


def _lexists(path: Path) -> bool:
    return path.is_symlink() or path.exists()


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _json_bytes(payload: dict) -> bytes:
    # ensure_ascii=False: the shipped manifests carry an em dash, and escaping it
    # would make every rewrite differ from the file it came from.
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _read_json_object(path: Path) -> dict:
    """`path` as a JSON object, or InstallError saying why it is not one.

    utf-8-sig, not utf-8: PowerShell 5.1's Set-Content writes a BOM by default,
    and json.loads rejects one.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"{path} could not be read as JSON ({exc}).") from exc
    if not isinstance(payload, dict):
        raise InstallError(f"{path} must contain a JSON object.")
    return payload


# --------------------------------------------------------------------------
# Staged, all-or-nothing writes
# --------------------------------------------------------------------------

# The one call every swap goes through, so a test can fail one partway.
_replace = os.replace


class _Op:
    """One planned change: write bytes, place a symlink, replace a tree, or remove."""

    def __init__(
        self,
        dest: Path,
        kind: str,
        data: bytes = b"",
        source: Path | None = None,
        tree: dict[str, object] | None = None,
    ) -> None:
        self.dest = dest
        self.kind = kind  # "bytes" | "link" | "tree" | "remove"
        self.data = data
        self.source = source
        self.tree = tree or {}
        self.staged: Path | None = None
        self.backup: Path | None = None
        self.placed = False


class Transaction:
    """Collect writes, then apply them all or none.

    Planning reads only. `commit` first writes every new file to a sibling of its
    destination -- the same directory, so the same filesystem, so the rename that
    follows is atomic on POSIX and Windows alike -- and only then swaps them in,
    moving each current file aside first. If any step fails, the swapped-in files
    are removed, the originals are moved back, and the error is raised with the
    target as it was.

    Moving the old file aside rather than writing over it is also what keeps a
    symlink from an earlier `--mode symlink` install from being written through
    into the plugin checkout it points at.
    """

    def __init__(self) -> None:
        self.token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.ops: list[_Op] = []
        self._created_dirs: list[Path] = []

    # -- planning ---------------------------------------------------------

    def _add(self, op: _Op) -> None:
        self.ops = [existing for existing in self.ops if existing.dest != op.dest]
        self.ops.append(op)

    def write(self, dest: Path, data: bytes) -> bool:
        if not dest.is_symlink() and dest.is_file():
            try:
                if dest.read_bytes() == data:
                    return False
            except OSError:
                pass
        self._add(_Op(dest, "bytes", data=data))
        return True

    def link(self, dest: Path, source: Path) -> bool:
        if same_link(dest, source):
            return False
        self._add(_Op(dest, "link", source=source))
        return True

    def install(self, dest: Path, source: Path, mode: str) -> bool:
        if mode == "symlink":
            return self.link(dest, source)
        return self.write(dest, source.read_bytes())

    def tree(self, dest: Path, source_dir: Path, mode: str) -> bool:
        """Replace the directory `dest` with a copy (or links) of `source_dir`.

        Whole-directory replacement is what removes a file an earlier version
        shipped and this one does not, such as a dropped skill.
        """
        desired: dict[str, object] = {}
        for source in sorted(source_dir.rglob("*")):
            if source.is_dir():
                continue
            relative = source.relative_to(source_dir).as_posix()
            desired[relative] = source if mode == "symlink" else source.read_bytes()
        if self._tree_matches(dest, desired):
            return False
        self._add(_Op(dest, "tree", tree=desired))
        return True

    @staticmethod
    def _tree_matches(dest: Path, desired: dict[str, object]) -> bool:
        if dest.is_symlink() or not dest.is_dir():
            return False
        present = {
            path.relative_to(dest).as_posix(): path
            for path in dest.rglob("*")
            if path.is_symlink() or not path.is_dir()
        }
        if set(present) != set(desired):
            return False
        for relative, want in desired.items():
            have = present[relative]
            if isinstance(want, Path):
                if not same_link(have, want):
                    return False
            elif have.is_symlink() or have.read_bytes() != want:
                return False
        return True

    def remove(self, dest: Path) -> bool:
        if not _lexists(dest):
            return False
        self._add(_Op(dest, "remove"))
        return True

    @property
    def changed(self) -> list[Path]:
        return [op.dest for op in self.ops]

    def changes(self, path: Path) -> bool:
        return any(op.dest == path for op in self.ops)

    # -- applying ---------------------------------------------------------

    def _sibling(self, dest: Path, tag: str) -> Path:
        return dest.parent / f".{dest.name}.ix-{tag}-{self.token}"

    def _mkdirs(self, directory: Path) -> None:
        missing: list[Path] = []
        probe = directory
        while not probe.exists():
            missing.append(probe)
            if probe.parent == probe:
                break
            probe = probe.parent
        for path in reversed(missing):
            path.mkdir()
            self._created_dirs.append(path)

    def _stage(self, op: _Op) -> None:
        if op.kind == "remove":
            return
        self._mkdirs(op.dest.parent)
        staged = self._sibling(op.dest, "new")
        op.staged = staged
        if op.kind == "bytes":
            with open(staged, "xb") as handle:
                handle.write(op.data)
            return
        if op.kind == "link":
            assert op.source is not None
            staged.symlink_to(op.source)
            return
        staged.mkdir()
        for relative, want in op.tree.items():
            path = staged / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(want, Path):
                path.symlink_to(want)
            else:
                path.write_bytes(want)  # type: ignore[arg-type]

    def commit(self) -> list[Path]:
        try:
            for op in self.ops:
                self._stage(op)
            for op in self.ops:
                if _lexists(op.dest):
                    backup = self._sibling(op.dest, "old")
                    _replace(op.dest, backup)
                    op.backup = backup
                if op.staged is not None:
                    _replace(op.staged, op.dest)
                    op.staged = None
                    op.placed = True
        except BaseException as exc:
            problems = self._rollback()
            detail = f"{type(exc).__name__}: {exc}"
            if problems:
                raise InstallError(
                    f"Install failed ({detail}) and could not be fully undone:\n  "
                    + "\n  ".join(problems)
                ) from exc
            raise InstallError(
                f"Install failed ({detail}). Nothing was changed; the previous files are in place."
            ) from exc
        self._finish()
        return self.changed

    def _rollback(self) -> list[str]:
        problems: list[str] = []
        for op in reversed(self.ops):
            try:
                if op.placed:
                    remove_path(op.dest)
                    op.placed = False
                if op.backup is not None:
                    _replace(op.backup, op.dest)
                    op.backup = None
            except OSError as exc:
                problems.append(f"{op.dest}: {exc}")
            if op.staged is not None:
                try:
                    if _lexists(op.staged):
                        remove_path(op.staged)
                except OSError:
                    pass
        for directory in reversed(self._created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        return problems

    def _finish(self) -> None:
        for op in self.ops:
            if op.backup is None:
                continue
            try:
                remove_path(op.backup)
            except OSError as exc:  # pragma: no cover - e.g. a file held open on Windows
                print(f"  [!!] could not remove {op.backup}: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# Plugin + marketplace
# --------------------------------------------------------------------------


def _read_plugin_json() -> dict:
    plugin_json = source_plugin_dir() / ".codex-plugin" / "plugin.json"
    try:
        with plugin_json.open(encoding="utf-8") as fh:
            meta = json.load(fh)
        return meta if isinstance(meta, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _normalise_local_path(value: object) -> str:
    text = str(value or "").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def plan_marketplace(
    tx: Transaction,
    marketplace_path: Path,
    plugin_path: str,
    marketplace_name: str,
    marketplace_display_name: str,
    force: bool,
    version: str = "",
    description: str = "",
) -> None:
    if _lexists(marketplace_path):
        payload = _read_json_object(marketplace_path)
        original = copy.deepcopy(payload)
    else:
        payload = {
            "name": marketplace_name,
            "interface": {"displayName": marketplace_display_name},
            "plugins": [],
        }
        original = None

    payload.setdefault("name", marketplace_name)
    interface = payload.setdefault("interface", {})
    if not isinstance(interface, dict):
        raise InstallError(f"{marketplace_path} field 'interface' must be an object.")
    interface.setdefault("displayName", marketplace_display_name)

    plugins = payload.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise InstallError(f"{marketplace_path} field 'plugins' must be an array.")

    new_entry: dict = {
        "name": PLUGIN_NAME,
        "source": {
            "source": "local",
            "path": plugin_path,
        },
        **PLUGIN_ENTRY,
    }
    if version:
        new_entry["version"] = version
    if description:
        new_entry["description"] = description

    for index, entry in enumerate(plugins):
        if not (isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME):
            continue
        source = entry.get("source")
        existing_path = source.get("path") if isinstance(source, dict) else None
        ours = _normalise_local_path(existing_path) == _normalise_local_path(plugin_path)
        if not ours and not force:
            raise InstallError(
                f"{marketplace_path} already has a plugin named '{PLUGIN_NAME}' at "
                f"{existing_path!r}, not {plugin_path!r}. Re-run with --force to replace it."
            )
        plugins[index] = entry if entry == new_entry else new_entry
        break
    else:
        plugins.append(new_entry)

    if payload != original:
        tx.write(marketplace_path, _json_bytes(payload))


def plan_plugin(
    tx: Transaction,
    target_root: Path,
    home_install: bool,
    mode: str,
    force: bool,
    marketplace_name: str,
    marketplace_display_name: str,
) -> list[Path]:
    if home_install:
        plugin_destination = target_root / ".codex" / "plugins" / PLUGIN_NAME
        plugin_path = f"./.codex/plugins/{PLUGIN_NAME}"
    else:
        plugin_destination = target_root / "plugins" / PLUGIN_NAME
        plugin_path = f"./plugins/{PLUGIN_NAME}"

    if not _same_location(plugin_destination, source_plugin_dir()):
        # Installing the checkout into itself: the tree is already the source.
        tx.tree(plugin_destination, source_plugin_dir(), mode)

    plugin_meta = _read_plugin_json()
    marketplace_path = target_root / ".agents" / "plugins" / "marketplace.json"
    plan_marketplace(
        tx,
        marketplace_path,
        plugin_path,
        marketplace_name,
        marketplace_display_name,
        force,
        version=str(plugin_meta.get("version", "")),
        description=str(plugin_meta.get("description", "")),
    )
    return [plugin_destination, marketplace_path]


# --------------------------------------------------------------------------
# config.toml (read-only, except to replace a stale MCP registration)
# --------------------------------------------------------------------------

# Codex 0.124 made lifecycle hooks a stable, default-on feature named `hooks`
# (openai/codex#19012). `codex_hooks`, which this installer used to write, is now
# a legacy alias (codex-rs/features/src/legacy.rs) that Codex reports as
# deprecated on every start. So nothing is written to config.toml for hooks; it
# is only read, to say so when the user's config turns hooks off.
_HOOK_FEATURE_KEYS = ("hooks", "codex_hooks")


def _load_toml(config_path: Path) -> dict | None:
    if tomllib is None:
        return None
    try:
        return tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def hooks_disabled_by(config_path: Path) -> str | None:
    """The `[features]` key in `config_path` that turns hooks off, if any.

    Read-only, and silent on anything it cannot read: a config this cannot parse
    is Codex's to report, not a reason to fail an install.
    """
    config = _load_toml(config_path)
    features = config.get("features") if config else None
    if not isinstance(features, dict):
        return None
    for key in _HOOK_FEATURE_KEYS:
        if features.get(key) is False:
            return key
    return None


def legacy_hooks_flag(config_path: Path) -> bool:
    """True when config.toml still carries the `codex_hooks = true` 2.4.2 and earlier wrote."""
    config = _load_toml(config_path)
    features = config.get("features") if config else None
    return isinstance(features, dict) and features.get("codex_hooks") is True


def codex_config_path() -> Path:
    """The config `codex mcp add` -- and so `ix mcp install` -- writes to."""
    codex_home = os.environ.get("CODEX_HOME", "")
    if codex_home and os.path.isabs(codex_home):
        return Path(codex_home) / "config.toml"
    return Path.home() / ".codex" / "config.toml"


_LEGACY_SERVER_RE = re.compile(r"(^|[/\\])mcp[/\\]server\.py$")


def stale_mcp_registration(config_path: Path) -> str | None:
    """The old server an `ix-memory` registration points at, if it is 2.4.1 or earlier's.

    2.4.1 and earlier copied a FastMCP server to `.codex/mcp/server.py` and told
    the user to run `codex mcp add ix-memory -- python3 <that path>`. `ix mcp
    install` reads such an entry as a different server and, without `--force`,
    leaves it in place -- so Codex kept launching a file the plugin had deleted.
    """
    config = _load_toml(config_path)
    servers = config.get("mcp_servers") if config else None
    entry = servers.get(PLUGIN_NAME) if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        return None
    args = entry.get("args")
    words = [entry.get("command")] + (list(args) if isinstance(args, list) else [])
    for word in words:
        if isinstance(word, str) and _LEGACY_SERVER_RE.search(word.strip()):
            return word.strip()
    return None


_MCP_TABLE_RE = re.compile(
    r"""^[ \t]*\[[ \t]*mcp_servers[ \t]*\.[ \t]*(?:ix-memory|"ix-memory"|'ix-memory')[ \t]*\][ \t]*(?:\#.*)?$"""
)
_ANY_TABLE_RE = re.compile(r"^[ \t]*\[")


def rewrite_mcp_registration(config_path: Path, command: str, args: list[str]) -> bool:
    """Point `[mcp_servers.ix-memory]` at `command args`, as `codex mcp add` would.

    The fallback for a CLI whose `ix mcp install` has no `--force`. Edits a copy,
    and swaps it in only if the copy parses and differs from the original in
    exactly that one entry; anything else is left for the user. Returns whether
    the file was rewritten.
    """
    if tomllib is None:
        return False
    try:
        text = config_path.read_text(encoding="utf-8-sig")
        before = tomllib.loads(text)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False

    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if _MCP_TABLE_RE.match(line)), None)
    if start is None:
        return False
    end = start + 1
    while end < len(lines) and not _ANY_TABLE_RE.match(lines[end]):
        end += 1
    body = [
        "[mcp_servers.ix-memory]\n",
        f"command = {json.dumps(command)}\n",
        f"args = {json.dumps(args)}\n",
    ]
    if end < len(lines):
        body.append("\n")
    updated = "".join(lines[:start] + body + lines[end:])

    try:
        after = tomllib.loads(updated)
    except tomllib.TOMLDecodeError:
        return False
    entry = after.get("mcp_servers", {}).get(PLUGIN_NAME, {})
    if entry.get("command") != command or entry.get("args") != args:
        return False
    for parsed in (before, after):
        parsed.get("mcp_servers", {}).pop(PLUGIN_NAME, None)
    if before != after:
        return False

    tx = Transaction()
    tx.write(config_path, updated.encode("utf-8"))
    tx.commit()
    return True


# --------------------------------------------------------------------------
# Hooks
# --------------------------------------------------------------------------


def _source_git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root()),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            rev = result.stdout.strip()
            return rev if rev else None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _installed_manifest(target_root: Path) -> dict:
    path = target_root / VERSION_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def plan_version_file(tx: Transaction, target_root: Path, files: list[str]) -> Path:
    """Plan `.codex/ix-plugin-version.json`: what is installed, and which files are the plugin's.

    Left alone when only the timestamp would change, so a reinstall that changes
    nothing writes nothing.
    """
    meta = _read_plugin_json()
    payload: dict = {
        "plugin_name": meta.get("name", PLUGIN_NAME),
        "plugin_version": meta.get("version", "unknown"),
        "source_path": str(repo_root()),
        "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
    commit = _source_git_commit()
    if commit:
        payload["git_commit"] = commit
    payload["files"] = sorted(files)

    version_path = target_root / VERSION_FILE
    current = _installed_manifest(target_root)
    if {k: v for k, v in current.items() if k != "installed_at"} != {
        k: v for k, v in payload.items() if k != "installed_at"
    } or tx.ops:
        tx.write(version_path, _json_bytes(payload))
    return version_path


HOOK_COMMAND_RE = re.compile(
    # The shipped command is `/bin/sh -c '... .codex/hooks/<name>.py ...'`. The
    # hook's own name is the only part that varies between the five entries, so
    # that is what gets recovered here. Anchored on the hooks directory so a
    # future command shape that still names the file keeps working.
    r"\.codex[/\\]hooks[/\\](?P<name>[A-Za-z_][A-Za-z0-9_]*)\.py"
)

# Every hook script any version has registered in hooks.json, plus the Windows
# launcher 2.4.x pointed commands at. A handler whose command names one of these
# is the plugin's, whatever else about it changed between versions: `-lc` became
# `-c`, `Bash` became `Bash|apply_patch`, Windows got `_launch.py`.
PLUGIN_HOOK_SCRIPTS = frozenset(
    {"session_start", "user_prompt_submit", "pre_tool_use", "post_tool_use", "stop", "_launch"}
)

_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def powershell_literal(text: str) -> str:
    """`text` as a PowerShell single-quoted literal: no `$` expansion, no escapes.

    PowerShell treats the typographic single quotes as quote characters too, so
    each of them is doubled along with the ASCII one.
    """
    return "'" + "".join(ch * 2 if ch in _PS_QUOTES else ch for ch in text) + "'"


def windows_hook_command(python: str, launcher: object, hook_name: str) -> str:
    """The `commandWindows` that launches `hook_name` without /bin/sh.

    Codex 0.155 runs a hook command through the session's shell
    (codex-rs/core/src/session/mod.rs, hooks config: `shell.derive_exec_args`),
    and on Windows that shell is PowerShell (shell-command/src/shell_detect.rs,
    `default_user_shell`): `powershell.exe -NoProfile -Command <command>`. There
    a command that starts with a quoted path is a string expression, so the old
    `"python" "launcher" name` was a parse error before any hook ran. The call
    operator `&` makes it an invocation; single quotes keep a `$` in a path from
    being expanded.

    Both paths are quoted: #349 is a live report from a user whose profile is
    `C:\\Users\\Win 10`.
    """
    return f"& {powershell_literal(python)} {powershell_literal(str(launcher))} {hook_name}"


def _plugin_script(command: object) -> str | None:
    if not isinstance(command, str):
        return None
    for match in HOOK_COMMAND_RE.finditer(command):
        if match.group("name") in PLUGIN_HOOK_SCRIPTS:
            return match.group("name")
    return None


def is_plugin_handler(handler: object) -> bool:
    if not isinstance(handler, dict):
        return False
    return any(
        _plugin_script(handler.get(key)) is not None for key in ("command", "commandWindows")
    )


def _add_windows_commands(payload: dict, python: str, launcher: object) -> bool:
    """Give every plugin handler a `commandWindows`. Returns whether anything changed.

    `command` keeps the POSIX form, so a repo-local hooks.json still works for
    a teammate on macOS or Linux; Codex picks `commandWindows` on Windows
    (hooks/src/engine/discovery.rs).
    """
    changed = False
    for blocks in payload["hooks"].values():
        for block in blocks:
            for hook in block.get("hooks", []):
                match = HOOK_COMMAND_RE.search(hook.get("command", ""))
                if not match or match.group("name") == "_launch":
                    continue
                desired = windows_hook_command(python, launcher, match.group("name"))
                if hook.get("commandWindows") != desired:
                    hook["commandWindows"] = desired
                    changed = True
    return changed


def render_hooks_json(source: Path, launcher: object) -> str | None:
    """The plugin's hooks.json for this machine, or None to use the source as-is.

    Off Windows the shipped file is already right. On Windows each handler gains
    a `commandWindows` naming this interpreter and the installed launcher.
    """
    if os.name != "nt":
        return None

    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    try:
        changed = _add_windows_commands(payload, sys.executable, launcher)
    except (AttributeError, KeyError, TypeError):
        return None

    if not changed:
        return None
    return json.dumps(payload, indent=2) + "\n"


def merge_hooks(existing: dict | None, ours: dict) -> dict:
    """`existing` with every plugin handler replaced by `ours`, everything else kept.

    The plugin's groups go where its first old one was, so the other hooks keep
    their positions -- Codex keys a hook's trust by event, group and handler
    index (hooks/src/engine/discovery.rs, `hook_key`), and shifting a user's hook
    would make Codex ask them to re-trust it.
    """
    if existing is None:
        return copy.deepcopy(ours)

    result = copy.deepcopy(existing)
    events = result.setdefault("hooks", {})
    ours_events = ours.get("hooks", {})

    for event in list(events):
        groups = events[event]
        kept: list = []
        insert_at: int | None = None
        for group in groups:
            handlers = group.get("hooks", [])
            theirs = [h for h in handlers if not is_plugin_handler(h)]
            if len(theirs) == len(handlers):
                kept.append(group)
                continue
            if insert_at is None:
                insert_at = len(kept)
            if theirs:
                kept.append({**group, "hooks": theirs})
        new_groups = ours_events.get(event, [])
        if insert_at is None and not new_groups:
            continue  # nothing of the plugin's here, before or after
        if insert_at is None:
            insert_at = len(kept)
        kept[insert_at:insert_at] = copy.deepcopy(new_groups)
        if kept:
            events[event] = kept
        else:
            del events[event]

    for event, groups in ours_events.items():
        if event not in events:
            events[event] = copy.deepcopy(groups)
    return result


def _hooks_shape_ok(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    events = payload.get("hooks", {})
    if not isinstance(events, dict):
        return False
    for groups in events.values():
        if not isinstance(groups, list):
            return False
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                return False
    return True


def plan_hooks(tx: Transaction, target_root: Path, mode: str, force: bool) -> list[Path]:
    paths: list[Path] = []
    codex_dir = target_root / ".codex"
    hooks_source = source_codex_dir() / "hooks"
    hooks_destination = codex_dir / "hooks"

    hooks_json = codex_dir / "hooks.json"
    hooks_json_source = source_codex_dir() / "hooks.json"
    source_bytes = hooks_json_source.read_bytes()

    if not _same_location(hooks_json, hooks_json_source):
        # Installing the checkout into itself is skipped: the Windows form names
        # this machine's interpreter, and writing it would leave the tracked
        # source carrying one developer's paths.
        rendered = render_hooks_json(hooks_json_source, hooks_destination / "_launch.py")
        ours_text = rendered if rendered is not None else source_bytes.decode("utf-8")
        ours = json.loads(ours_text)

        existing: dict | None = None
        if _lexists(hooks_json):
            try:
                existing = json.loads(hooks_json.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                if not force:
                    raise InstallError(
                        f"{hooks_json} is not valid JSON ({exc}), so the Ix hooks cannot be "
                        "merged into it. Fix it, or re-run with --force to replace it."
                    ) from exc
                existing = None
            else:
                if not _hooks_shape_ok(existing):
                    if not force:
                        raise InstallError(
                            f"{hooks_json} is not in the shape Codex reads "
                            '({"hooks": {"<Event>": [{"hooks": [...]}]}}). '
                            "Fix it, or re-run with --force to replace it."
                        )
                    existing = None

        merged = merge_hooks(existing, ours)
        if merged == ours and mode == "symlink" and rendered is None:
            tx.link(hooks_json, hooks_json_source)
        elif merged == ours:
            tx.write(hooks_json, ours_text.encode("utf-8"))
        elif existing is None or merged != existing or hooks_json.is_symlink():
            tx.write(hooks_json, _json_bytes(merged))
    paths.append(hooks_json)

    shipped: list[str] = []
    for source in sorted(hooks_source.glob("*.py")):
        destination = hooks_destination / source.name
        relative = destination.relative_to(target_root).as_posix()
        shipped.append(relative)
        if not _same_location(destination, source):
            tx.install(destination, source, mode)
        paths.append(destination)

    # Files an earlier version installed that this one does not ship.
    previous = _installed_manifest(target_root).get("files")
    owned = previous if isinstance(previous, list) else list(LEGACY_HOOK_FILES)
    for relative in owned:
        if not isinstance(relative, str) or relative in shipped:
            continue
        if not re.fullmatch(r"\.codex/hooks/[A-Za-z0-9_]+\.py", relative):
            continue  # never act on a path outside the hooks directory
        tx.remove(target_root / relative)

    paths.append(plan_version_file(tx, target_root, shipped))
    return paths


def install_hooks(target_root: Path, mode: str, force: bool) -> list[Path]:
    tx = Transaction()
    paths = plan_hooks(tx, target_root, mode, force)
    tx.commit()
    return paths


# --------------------------------------------------------------------------
# Leftovers from earlier versions
# --------------------------------------------------------------------------

_OLD_CACHE_FILE_RE = re.compile(
    r"ix-(status|pro|runtime-health)\.json"
    r"|ix-briefing(-[0-9a-f]{16})?\.txt"
    r"|auto-map-[0-9a-f]{16}(\.[A-Za-z]+)?"
)


def legacy_cache_dirs() -> list[Path]:
    """Where hooks before 2.4.2 kept their cache: `$TMPDIR/ix-codex-hooks`, else /tmp."""
    candidates = [
        Path(os.environ.get("TMPDIR") or "/tmp") / "ix-codex-hooks",
        Path(tempfile.gettempdir()) / "ix-codex-hooks",
    ]
    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def remove_legacy_cache_dir(path: Path) -> bool:
    """Delete an old shared cache dir, only if it is this user's and holds only cache files.

    It lived in a world-writable directory, so its name alone proves nothing: a
    directory someone else created, a symlink, or one with anything unexpected
    in it is left exactly as it is.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        return False
    try:
        entries = list(os.scandir(path))
    except OSError:
        return False
    for entry in entries:
        if not entry.is_file(follow_symlinks=False) or not _OLD_CACHE_FILE_RE.fullmatch(entry.name):
            return False
    try:
        for entry in entries:
            os.unlink(entry.path)
        os.rmdir(path)
    except OSError:
        return False
    return True


def remove_legacy_mcp_files(target_root: Path, config_path: Path) -> list[Path]:
    """Delete the MCP server 2.4.1 and earlier copied in, once nothing launches it.

    Not part of the transaction: it has to wait for the registration step, and a
    Codex config that still points at the file -- because `ix` was missing, or
    the user declined to replace a conflicting entry -- keeps it in place.
    """
    try:
        config_text = config_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        config_text = ""
    # Deliberately coarse: any registration still naming an `mcp/server.py`
    # keeps these. A path compared exactly would miss the same file reached
    # through a symlinked home, and deleting a server Codex still launches is
    # the one outcome worse than leaving two stale files behind.
    if "mcp/server.py" in config_text.replace("\\\\", "/").replace("\\", "/"):
        return []

    removed: list[Path] = []
    for relative in LEGACY_MCP_FILES:
        path = target_root / relative
        if not path.is_file() or path.is_symlink():
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    mcp_dir = target_root / ".codex" / "mcp"
    cache = mcp_dir / "__pycache__"
    if removed and cache.is_dir() and not cache.is_symlink():
        stale = [p for p in cache.iterdir() if re.fullmatch(r"(server|ix_llm)\..*\.pyc", p.name)]
        if len(stale) == len(list(cache.iterdir())):
            for pyc in stale:
                pyc.unlink(missing_ok=True)
            try:
                cache.rmdir()
            except OSError:
                pass
    try:
        if removed:
            mcp_dir.rmdir()  # only succeeds if empty
    except OSError:
        pass
    return removed


# --------------------------------------------------------------------------
# MCP registration
# --------------------------------------------------------------------------

MIN_IX_VERSION_FOR_MCP = (0, 9, 3)

# `ix mcp install --host codex` asks `codex mcp list` (20s cap inside the CLI)
# and then runs `codex mcp add`. This bounds the whole thing, so a wedged host
# CLI cannot hang the installer.
MCP_INSTALL_TIMEOUT_SECONDS = 120


def _mcp_timeout() -> float:
    raw = os.environ.get("IX_CODEX_MCP_TIMEOUT", "")
    try:
        value = float(raw)
    except ValueError:
        return MCP_INSTALL_TIMEOUT_SECONDS
    return value if value > 0 else MCP_INSTALL_TIMEOUT_SECONDS


def _ix_executable() -> str | None:
    """The `ix` on PATH, as an absolute path, or None if there is none.

    Resolved rather than spawned by name because this script runs on Windows,
    where npm ships no `ix.exe` — only `ix.CMD` — and CreateProcess consults no
    PATHEXT, so `subprocess.run(["ix", ...])` raises FileNotFoundError however
    well-formed the rest of the argv is. `shutil.which` DOES apply PATHEXT, so it
    finds the shim. This is Ix#383's inner half, which `.codex/hooks/common.py`
    already fixes for the hooks; every new `ix` call site has to do the same.
    """
    executable = shutil.which("ix", path=os.environ.get("PATH"))
    if executable is None or not os.path.isabs(executable):
        return None
    return executable


def _ix_version(executable: str) -> tuple[int, ...] | None:
    """The installed CLI's version, or None if it could not be read.

    Takes the resolved path rather than resolving its own, so the version that
    gates the registration and the binary that performs it cannot be two
    different installs.
    """
    try:
        out = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=30
        ).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
    return tuple(int(g) for g in match.groups()) if match else None


def install_mcp(target_root: Path, mode: str, force: bool) -> list[Path]:
    """Register the CLI's own MCP server rather than shipping one.

    This plugin used to install `mcp/server.py`, a FastMCP server whose 23 tools
    each shelled out to the `ix` CLI. The CLI now serves the same tools itself
    via `ix mcp`, in-process, so the copy here was a second implementation of
    one surface. Nothing is installed now; the CLI is pointed at instead.
    """
    del target_root, mode, force  # nothing is copied any more
    return []


class McpResult:
    def __init__(self, ok: bool, message: str) -> None:
        self.ok = ok
        self.message = message


def _first_lines(text: str | None, limit: int = 5) -> str:
    lines = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    return "\n".join(f"      {line}" for line in lines[:limit])


def _codex_outcome(stdout: str | None) -> dict | None:
    """The `codex` row of `ix mcp install --format json`, if the output was that."""
    text = (stdout or "").strip()
    start = text.find("{")
    if start == -1:
        return None
    try:
        report, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    hosts = report.get("hosts") if isinstance(report, dict) else None
    for host in hosts if isinstance(hosts, list) else []:
        if isinstance(host, dict) and host.get("id") == "codex":
            return host
    return None


def register_mcp(executable: str, force: bool) -> McpResult:
    """Run `ix mcp install --host codex`, bounded, and say plainly how it went."""
    config_path = codex_config_path()
    stale = stale_mcp_registration(config_path)
    argv = [executable, "mcp", "install", "--host", "codex", "--format", "json"]
    if stale or force:
        # A registration of the deleted server.py is ours to replace; `ix mcp
        # install` cannot tell it from someone else's server and needs --force.
        argv.append("--force")

    timeout = _mcp_timeout()
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return McpResult(
            False,
            f"`ix mcp install --host codex` did not finish within {timeout:g}s and was stopped. "
            "Run it yourself once Codex is responsive.",
        )
    except (OSError, ValueError) as exc:
        return McpResult(False, f"could not run `{executable} mcp install`: {exc}")

    if result.returncode != 0 and "--force" in argv and "--force" in (result.stderr or "") \
            and "unknown option" in (result.stderr or ""):
        # A CLI without the flag. Do what `codex mcp add` would have done to the
        # entry -- only possible for the stale registration recognised above.
        if stale:
            command = executable if os.name == "nt" else "ix"
            if rewrite_mcp_registration(config_path, command, ["mcp"]):
                return McpResult(True, f"Replaced the old ix-memory server ({stale}) with `ix mcp`.")
        return McpResult(
            False,
            "this `ix mcp install` has no --force, so the old ix-memory registration "
            f"in {config_path} could not be replaced. Run `codex mcp remove ix-memory`, "
            "then re-run the installer.",
        )

    host = _codex_outcome(result.stdout)
    outcome = host.get("outcome") if host else None
    if result.returncode != 0 or outcome == "failed":
        detail = _first_lines(result.stderr) or _first_lines(result.stdout)
        note = f" ({host.get('note')})" if host and host.get("note") else ""
        return McpResult(
            False,
            f"`ix mcp install --host codex` failed (exit {result.returncode}){note}."
            + (f"\n{detail}" if detail else ""),
        )
    if outcome in ("registered", "already-registered"):
        verb = "Registered" if outcome == "registered" else "Already registered:"
        replaced = f" (replaced the old {stale})" if stale and outcome == "registered" else ""
        return McpResult(True, f"{verb} `ix mcp` as the ix-memory MCP server in Codex{replaced}.")
    if outcome == "conflict":
        return McpResult(
            False,
            f"{config_path} already has an ix-memory MCP server that is not `ix mcp`; it was "
            "left alone. Re-run with --force to replace it.",
        )
    if outcome == "not-installed":
        return McpResult(
            False,
            "the Codex CLI (`codex`) is not on PATH, so nothing was registered. Run "
            "`ix mcp install --host codex` once it is.",
        )
    if outcome is None:
        # A CLI that printed something other than the JSON asked for. Its exit
        # status is all there is to go on.
        return McpResult(True, "Ran `ix mcp install --host codex`.")
    return McpResult(False, f"`ix mcp install --host codex` reported '{outcome}'.")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def warn(message: str) -> None:
    print(f"  [!!] {message}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    target_root = target_root_from_args(args)
    target_root.mkdir(parents=True, exist_ok=True)

    tx = Transaction()
    installed: list[Path] = []
    hooks_json = target_root / ".codex" / "hooks.json"
    try:
        if args.plugin:
            installed.extend(
                plan_plugin(
                    tx,
                    target_root,
                    args.home,
                    args.mode,
                    args.force,
                    args.marketplace_name,
                    args.marketplace_display_name,
                )
            )
        if args.hooks:
            installed.extend(plan_hooks(tx, target_root, args.mode, args.force))
        hooks_changed = tx.changes(hooks_json)
        changed = tx.commit()
    except InstallError as exc:
        print(f"  [error] {exc}", file=sys.stderr)
        return 1

    print(f"Installed into: {target_root}")
    for path in installed:
        print(path)
    if not changed:
        print("Everything was already up to date.")
    else:
        for path in changed:
            if not _lexists(path):
                print(f"Removed (no longer shipped): {path}")

    if args.plugin:
        print("Plugin files and marketplace entry were installed.")
        print("The plugin is not active yet, so its skills will not appear immediately.")
        print("Restart Codex, then install or enable 'ix-memory' from the marketplace.")
        print("After that, type '$ix-tutorial' manually in chat.")
        print("Local Codex plugins do not reliably expose skill autocomplete or slash popups.")
    if args.hooks:
        if hooks_changed:
            print("Restart Codex so it reloads hooks.json.")
            # Codex runs a non-managed hook only once its exact definition has
            # been reviewed and trusted, and re-asks whenever that definition
            # changes (codex-rs/hooks/src/engine/discovery.rs, hook_trust_status).
            print("Codex will ask you to review and trust the Ix hooks on its next start;")
            print("until you do, it skips them. `codex exec` cannot ask, so it skips them too.")
        else:
            print("hooks.json is unchanged, so hooks you already trusted stay trusted.")
        config_toml = target_root / ".codex" / "config.toml"
        disabled = hooks_disabled_by(config_toml)
        if disabled is not None:
            print(f"Hooks are turned off in .codex/config.toml ([features] {disabled} = false).")
            print("Remove that line, or set `hooks = true`, for the Ix hooks to run.")
        elif legacy_hooks_flag(config_toml):
            print("`codex_hooks = true` in .codex/config.toml was written by an older installer.")
            print("Codex now enables hooks by default and warns about that flag; you can delete it.")
        for cache in legacy_cache_dirs():
            if remove_legacy_cache_dir(cache):
                print(f"Removed the old hook cache {cache}.")

    if args.mcp:
        executable = _ix_executable()
        version = _ix_version(executable) if executable is not None else None
        if version is None:
            warn("Could not run `ix --version`; install the Ix CLI, then re-run with --mcp.")
        elif version < MIN_IX_VERSION_FOR_MCP:
            wanted = ".".join(str(part) for part in MIN_IX_VERSION_FOR_MCP)
            got = ".".join(str(part) for part in version)
            warn(f"The MCP server needs Ix CLI >= {wanted} (found {got}). Run `ix upgrade`.")
        else:
            # `ix mcp install` owns the per-host detail — including resolving the
            # launcher on the Windows where npm ships ix.CMD and no ix.exe.
            # Reaching it has the same problem one level up, so the resolved path
            # is what is spawned.
            print("Registering the Ix MCP server with Codex:")
            outcome = register_mcp(executable, args.force)
            if outcome.ok:
                print(f"  {outcome.message}")
            else:
                warn(f"MCP registration: {outcome.message}")
        for path in remove_legacy_mcp_files(target_root, codex_config_path()):
            print(f"Removed the old MCP server file {path}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
