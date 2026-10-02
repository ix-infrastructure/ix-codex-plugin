#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        # Only used to read config.toml for a hooks-disabled warning, which is
        # skipped without a parser.
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the ix-codex-plugin package, hooks, and MCP into a repo or home config."
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
        help="Overwrite conflicting files and replace an existing marketplace entry",
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
    args = parser.parse_args()
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


def same_file_contents(source: Path, destination: Path) -> bool:
    return destination.exists() and source.read_bytes() == destination.read_bytes()


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def install_file(source: Path, destination: Path, mode: str, force: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    if mode == "symlink":
        if destination.exists() or destination.is_symlink():
            if same_link(destination, source):
                return
            if not force:
                raise FileExistsError(f"{destination} already exists. Re-run with --force.")
            remove_path(destination)
        destination.symlink_to(source)
        return

    if destination.exists():
        if same_file_contents(source, destination):
            return
        if not force:
            raise FileExistsError(f"{destination} already exists. Re-run with --force.")
    shutil.copy2(source, destination)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:  # pragma: no cover - unresolvable path
        return False


def install_rendered(destination: Path, content: str, force: bool) -> None:
    """install_file for content generated here rather than copied from the tree.

    No symlink mode: the caller reaches this precisely because the file it needs
    differs from the source, and a link would point at the wrong bytes. An
    existing symlink is replaced -- rewriting through it would edit the checkout.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    desired = content.encode("utf-8")

    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        if destination.read_bytes() == desired:
            return
        if not force:
            raise FileExistsError(f"{destination} already exists. Re-run with --force.")

    destination.write_bytes(desired)


def install_tree(source_dir: Path, destination_dir: Path, mode: str, force: bool) -> None:
    for source in sorted(source_dir.rglob("*")):
        relative = source.relative_to(source_dir)
        destination = destination_dir / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        install_file(source, destination, mode, force)


def load_json(path: Path) -> dict:
    with path.open() as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def _read_plugin_json() -> dict:
    plugin_json = source_plugin_dir() / ".codex-plugin" / "plugin.json"
    try:
        with plugin_json.open() as fh:
            meta = json.load(fh)
        return meta if isinstance(meta, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def update_marketplace(
    marketplace_path: Path,
    plugin_path: str,
    marketplace_name: str,
    marketplace_display_name: str,
    force: bool,
    version: str = "",
    description: str = "",
) -> None:
    if marketplace_path.exists():
        payload = load_json(marketplace_path)
    else:
        payload = {
            "name": marketplace_name,
            "interface": {"displayName": marketplace_display_name},
            "plugins": [],
        }

    payload.setdefault("name", marketplace_name)
    interface = payload.setdefault("interface", {})
    if not isinstance(interface, dict):
        raise ValueError(f"{marketplace_path} field 'interface' must be an object.")
    interface.setdefault("displayName", marketplace_display_name)

    plugins = payload.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise ValueError(f"{marketplace_path} field 'plugins' must be an array.")

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
        if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME:
            if entry == new_entry:
                write_json(marketplace_path, payload)
                return
            if not force:
                raise FileExistsError(
                    f"{marketplace_path} already has a plugin entry named '{PLUGIN_NAME}'. "
                    "Re-run with --force to replace it."
                )
            plugins[index] = new_entry
            write_json(marketplace_path, payload)
            return

    plugins.append(new_entry)
    write_json(marketplace_path, payload)


# Codex 0.124 made lifecycle hooks a stable, default-on feature named `hooks`
# (openai/codex#19012). `codex_hooks`, which this installer used to write, is now
# a legacy alias (codex-rs/features/src/legacy.rs) that Codex reports as
# deprecated on every start. So nothing is written to config.toml any more; it is
# only read, to say so when the user's config turns hooks off.
_HOOK_FEATURE_KEYS = ("hooks", "codex_hooks")


def hooks_disabled_by(config_path: Path) -> str | None:
    """The `[features]` key in `config_path` that turns hooks off, if any.

    Read-only, and silent on anything it cannot read: a config this cannot parse
    is Codex's to report, not a reason to fail an install.
    """
    if tomllib is None:
        return None
    try:
        features = tomllib.loads(config_path.read_text(encoding="utf-8-sig")).get("features")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(features, dict):
        return None
    for key in _HOOK_FEATURE_KEYS:
        if features.get(key) is False:
            return key
    return None


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


def write_version_file(target_root: Path) -> Path:
    """Write .codex/ix-plugin-version.json into the install target."""
    meta = _read_plugin_json()
    plugin_version = meta.get("version", "unknown")
    plugin_name = meta.get("name", PLUGIN_NAME)

    payload: dict = {
        "plugin_name": plugin_name,
        "plugin_version": plugin_version,
        "source_path": str(repo_root()),
        "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    commit = _source_git_commit()
    if commit:
        payload["git_commit"] = commit

    version_path = target_root / ".codex" / "ix-plugin-version.json"
    version_path.parent.mkdir(parents=True, exist_ok=True)
    with version_path.open("w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    return version_path


def shell_free_hook_command(python: str, launcher: Path, hook_name: str) -> str:
    """The `hooks.json` command for `hook_name`, with no shell in it.

    Both paths are quoted: #349 is a live report from a user whose profile is
    `C:\\Users\\Win 10`, so a space in either path is a case that actually
    happens rather than a hypothetical.
    """
    return f'"{python}" "{launcher}" {hook_name}'


HOOK_COMMAND_RE = re.compile(
    # The shipped command is `/bin/sh -lc '... .codex/hooks/<name>.py ...'`. The
    # hook's own name is the only part that varies between the five entries, so
    # that is what gets recovered here. Anchored on the hooks directory so a
    # future command shape that still names the file keeps working.
    r"\.codex[/\\]hooks[/\\](?P<name>[A-Za-z_][A-Za-z0-9_]*)\.py"
)


def render_hooks_json(source: Path, launcher: Path) -> str | None:
    """The hooks.json this machine should have, or None to install the source as-is.

    The rewrite has to be expressed as *desired content* rather than as an edit
    applied afterwards. Editing after install_file makes the installed file
    permanently differ from the source, so the next run's content comparison
    fails and raises FileExistsError -- and the documented Windows one-liner
    (`irm ... | iex`) never passes --force, so every update would die, after
    install_plugin had already written and before install_mcp ran. Comparing
    against what this platform is supposed to end up with is idempotent by
    construction.

    utf-8-sig, not utf-8: PowerShell 5.1's Set-Content writes a BOM by default,
    and json.loads rejects one. Reading it as plain utf-8 meant a BOM'd file
    raised, the rewrite was skipped, and the installer reported success with all
    five hooks still dead -- the exact failure this exists to remove. The same
    trap, and the same fix, as hooks_disabled_by above.
    """
    if os.name != "nt":
        return None

    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # A hooks.json we cannot parse is not one we should rewrite. Leave it
        # for the user to see rather than replacing it with a guess.
        return None

    try:
        changed = _rewrite_hook_commands(payload, launcher)
    except (AttributeError, KeyError, TypeError):
        # Parsed, but not the shape we understand. Same reasoning as above: this
        # used to escape the JSON guard and abort the installer with a traceback.
        return None

    if not changed:
        return None
    return json.dumps(payload, indent=2) + "\n"


def _rewrite_hook_commands(payload: dict, launcher: Path) -> bool:
    changed = False
    for blocks in payload["hooks"].values():
        for block in blocks:
            for hook in block.get("hooks", []):
                command = hook.get("command", "")
                if "/bin/sh" not in command:
                    continue  # already rewritten, or never was a shell command
                match = HOOK_COMMAND_RE.search(command)
                if not match:
                    continue
                hook["command"] = shell_free_hook_command(
                    sys.executable, launcher, match.group("name")
                )
                changed = True
    return changed




def install_hooks(target_root: Path, mode: str, force: bool) -> list[Path]:
    installed: list[Path] = []
    codex_dir = target_root / ".codex"
    hooks_source = source_codex_dir() / "hooks"
    hooks_destination = codex_dir / "hooks"

    hooks_json = codex_dir / "hooks.json"
    hooks_json_source = source_codex_dir() / "hooks.json"
    rendered = render_hooks_json(hooks_json_source, hooks_destination / "_launch.py")
    if rendered is not None and _same_path(hooks_json, hooks_json_source):
        # Installing the checkout into itself. The rendered file names this
        # machine's interpreter, so writing it here would leave the tracked
        # source modified and every later diff carrying one developer's paths.
        rendered = None
    if rendered is None:
        install_file(source_codex_dir() / "hooks.json", hooks_json, mode, force)
    else:
        # Windows: the file this machine needs is not the source, so it cannot be
        # symlinked to it either -- the rendered command names this interpreter's
        # absolute path. Compare against the rendered content so a second run is
        # a no-op rather than a FileExistsError.
        install_rendered(hooks_json, rendered, force)
    installed.append(hooks_json)

    hooks_destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(hooks_source.glob("*.py")):
        destination = hooks_destination / source.name
        install_file(source, destination, mode, force)
        installed.append(destination)

    installed.append(write_version_file(target_root))

    return installed


def install_plugin(
    target_root: Path,
    home_install: bool,
    mode: str,
    force: bool,
    marketplace_name: str,
    marketplace_display_name: str,
) -> list[Path]:
    installed: list[Path] = []

    if home_install:
        plugin_destination = target_root / ".codex" / "plugins" / PLUGIN_NAME
        plugin_path = f"./.codex/plugins/{PLUGIN_NAME}"
    else:
        plugin_destination = target_root / "plugins" / PLUGIN_NAME
        plugin_path = f"./plugins/{PLUGIN_NAME}"

    install_tree(source_plugin_dir(), plugin_destination, mode, force)
    installed.append(plugin_destination)

    plugin_meta = _read_plugin_json()
    plugin_version = str(plugin_meta.get("version", ""))
    plugin_description = str(plugin_meta.get("description", ""))

    marketplace_path = target_root / ".agents" / "plugins" / "marketplace.json"
    update_marketplace(
        marketplace_path,
        plugin_path,
        marketplace_name,
        marketplace_display_name,
        force,
        version=plugin_version,
        description=plugin_description,
    )
    installed.append(marketplace_path)

    return installed


MIN_IX_VERSION_FOR_MCP = (0, 9, 3)


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
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
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


def main() -> None:
    args = parse_args()
    target_root = target_root_from_args(args)
    target_root.mkdir(parents=True, exist_ok=True)

    installed: list[Path] = []
    if args.plugin:
        installed.extend(
            install_plugin(
                target_root,
                args.home,
                args.mode,
                args.force,
                args.marketplace_name,
                args.marketplace_display_name,
            )
        )
    if args.hooks:
        installed.extend(install_hooks(target_root, args.mode, args.force))
    if args.mcp:
        installed.extend(install_mcp(target_root, args.mode, args.force))

    print(f"Installed into: {target_root}")
    for path in installed:
        print(path)

    if args.plugin:
        print("Plugin files and marketplace entry were installed.")
        print("The plugin is not active yet, so its skills will not appear immediately.")
        print("Restart Codex, then install or enable 'ix-memory' from the marketplace.")
        print("After that, type '$ix-tutorial' manually in chat.")
        print("Local Codex plugins do not reliably expose skill autocomplete or slash popups.")
    if args.hooks:
        print("Restart Codex so it reloads hooks.json.")
        # Codex runs a non-managed hook only once its exact definition has been
        # reviewed and trusted, and re-asks whenever that definition changes
        # (codex-rs/hooks/src/engine/discovery.rs, hook_trust_status).
        print("Codex will ask you to review and trust the Ix hooks on its next start;")
        print("until you do, it skips them. `codex exec` cannot ask, so it skips them too.")
        disabled = hooks_disabled_by(target_root / ".codex" / "config.toml")
        if disabled is not None:
            print(f"Hooks are turned off in .codex/config.toml ([features] {disabled} = false).")
            print("Remove that line, or set `hooks = true`, for the Ix hooks to run.")
    if args.mcp:
        executable = _ix_executable()
        version = _ix_version(executable) if executable is not None else None
        if version is None:
            print("Could not run `ix --version`; install the Ix CLI, then re-run with --mcp.")
        elif version < MIN_IX_VERSION_FOR_MCP:
            wanted = ".".join(str(part) for part in MIN_IX_VERSION_FOR_MCP)
            got = ".".join(str(part) for part in version)
            print(f"The MCP server needs Ix CLI >= {wanted} (found {got}). Run `ix upgrade`.")
        else:
            # `ix mcp install` owns the per-host detail — including resolving the
            # launcher for the seven hosts it registers, on the Windows where npm
            # ships ix.CMD and no ix.exe. Reaching it has the same problem one
            # level up, so the resolved path is what is spawned: the bare name
            # would raise FileNotFoundError before `check=False` had any say, and
            # the registration this flag exists to write would never happen.
            print("Registering the Ix MCP server with Codex:")
            subprocess.run([executable, "mcp", "install", "--host", "codex"], check=False)


if __name__ == "__main__":
    main()
