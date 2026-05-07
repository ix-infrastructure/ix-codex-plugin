#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
from pathlib import Path


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
        help="Install the ix-memory MCP server and print the codex mcp add registration command",
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


def ensure_codex_hooks_enabled(config_path: Path) -> None:
    if not config_path.exists():
        install_file(source_codex_dir() / "config.toml", config_path, "copy", force=False)
        return

    text = config_path.read_text()
    if "codex_hooks = true" in text:
        return

    lines = text.splitlines()
    inserted = False
    for index, line in enumerate(lines):
        if line.strip() == "[features]":
            insert_at = index + 1
            while insert_at < len(lines) and not lines[insert_at].startswith("["):
                insert_at += 1
            lines.insert(insert_at, "codex_hooks = true")
            inserted = True
            break

    if not inserted:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[features]", "codex_hooks = true"])

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(lines) + "\n")


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


def install_hooks(target_root: Path, mode: str, force: bool) -> list[Path]:
    installed: list[Path] = []
    codex_dir = target_root / ".codex"
    hooks_source = source_codex_dir() / "hooks"
    hooks_destination = codex_dir / "hooks"

    ensure_codex_hooks_enabled(codex_dir / "config.toml")
    installed.append(codex_dir / "config.toml")

    install_file(source_codex_dir() / "hooks.json", codex_dir / "hooks.json", mode, force)
    installed.append(codex_dir / "hooks.json")

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


def install_mcp(target_root: Path, mode: str, force: bool) -> list[Path]:
    installed: list[Path] = []
    mcp_source = repo_root() / "mcp" / "server.py"
    mcp_dest_dir = target_root / ".codex" / "mcp"
    mcp_dest = mcp_dest_dir / "server.py"
    mcp_dest_dir.mkdir(parents=True, exist_ok=True)
    install_file(mcp_source, mcp_dest, mode, force)
    installed.append(mcp_dest)
    return installed


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
        print("Restart Codex so it reloads .codex/config.toml and hooks.json.")
    if args.mcp:
        mcp_path = target_root / ".codex" / "mcp" / "server.py"
        print(f"Register the MCP server with Codex:")
        print(f"  codex mcp add ix-memory -- python3 {mcp_path}")


if __name__ == "__main__":
    main()
