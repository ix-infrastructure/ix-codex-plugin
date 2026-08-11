#!/usr/bin/env python3
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
        # Two things depend on this now, not one: reading the config to decide
        # whether an edit is needed at all, and proving the result parses before
        # writing it. Without either, the spellings enable_codex_hooks cannot
        # edit -- an inline table, a dotted key -- get a second [features]
        # appended and the invalid result is written out, which is the bug this
        # whole path exists to prevent. So on <3.11 without tomli it declines to
        # touch an existing config rather than risk corrupting it.
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


# Both bracket forms. `[[x]]` is an array-of-tables header, not a table named
# "[x": matching only `[x]` left it unrecognised, so it did not end the preceding
# section and every line under it was still attributed to [features]. A
# `codex_hooks` key inside one was then collapsed as a duplicate and deleted --
# silently, and the result still parses, so the tomllib guard below cannot catch
# it. That is worse in kind than the corruption this function exists to fix.
#
# The name is `.*?` rather than a "no brackets" class because brackets are legal
# inside a quoted key -- `[projects."C:\repos\proj[old]"]` is a real header, and
# Codex writes real repo paths into these. Excluding them left that header
# unrecognised too, which is the same silent-deletion bug as `[[x]]` by another
# route. The anchors still do the work: the line must start with `[` and end with
# `]`, so `x = [1]` and `[features] # note` are unaffected.
_TABLE_RE = re.compile(r"^\s*(\[\[?)\s*(.*?)\s*\]\]?\s*$")
# Quoted spellings too: TOML treats `"codex_hooks"` and `codex_hooks` as the same
# key, so missing the quoted form meant inserting a second assignment beside it
# and producing a duplicate the output check then rejected. The rewrite
# normalises it to the bare form, which is the same key.
_CODEX_HOOKS_RE = re.compile(r"""^(\s*)(?:codex_hooks|"codex_hooks"|'codex_hooks')\s*=""")


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


_MENTIONS_FEATURES_RE = re.compile(r"""^\s*(?:\[\[?\s*)?["']?features\b""", re.MULTILINE)


def _consume_line(line: str, open_delimiter: str | None) -> tuple[str | None, int]:
    """Scan one line; return the still-open triple-quote delimiter and `[` - `]`.

    Quote state has to survive the return, not just the loop: a multi-line string
    spans lines, so tracking it per line means an unbalanced `[` inside one is
    counted as opening an array that never closes, and every following line is
    read as part of a value. The [features] header then goes unseen and a second
    one is appended.
    """
    delta = 0
    index = 0
    while index < len(line):
        if open_delimiter is not None:
            # A multi-line *basic* string honours escapes, so `\""" ` does not
            # close it; a literal ''' string has none. Missing that ended the
            # string early, and the flag was then written into whichever table
            # the scanner thought it was in -- valid TOML, so the output check
            # cannot catch it.
            if open_delimiter == '"""' and line[index] == "\\":
                index += 2
                continue
            if line.startswith(open_delimiter, index):
                index += 3
                open_delimiter = None
                continue
            index += 1
            continue
        if line.startswith('"""', index) or line.startswith("'''", index):
            open_delimiter = line[index : index + 3]
            index += 3
            continue
        char = line[index]
        if char == "#":
            break
        if char in "\"'":
            # A single-line string. Only basic strings honour backslash escapes;
            # in a literal string a backslash is just a character, which is why
            # Windows paths are written that way and why this has to distinguish.
            index += 1
            while index < len(line):
                if char == '"' and line[index] == "\\":
                    index += 2
                    continue
                if line[index] == char:
                    index += 1
                    break
                index += 1
            continue
        if char == "[":
            delta += 1
        elif char == "]":
            delta -= 1
        index += 1
    return open_delimiter, delta


def _top_level_lines(lines: list[str]):
    """Yield (index, line, at_top_level) for every line.

    `at_top_level` is false inside a multi-line array or string, which is what
    separates a header from a value that looks like one: `["a[1]"]` is both a
    valid quoted-key header and a plausible array element, and no line-level
    pattern can tell them apart.
    """
    open_delimiter: str | None = None
    depth = 0
    for index, line in enumerate(lines):
        at_top_level = open_delimiter is None and depth == 0
        open_delimiter, delta = _consume_line(line, open_delimiter)
        depth = max(0, depth + delta)
        yield index, line, at_top_level


def _is_features_table(name: str) -> bool:
    """True for every spelling of the bare `[features]` table.

    `["features"]` is the same table as `[features]` -- TOML strips the quotes --
    so treating only the bare form as a match meant appending a second one beside
    it. Not `[features.sub]`: that is a different table, and one this can safely
    append a `[features]` after.
    """
    if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'":
        name = name[1:-1]
    return name == "features"


def _has_features_header(text: str) -> bool:
    """True when a `[features]` table header is present to edit."""
    lines = text.splitlines()
    for _index, line, at_top_level in _top_level_lines(lines):
        header = _TABLE_RE.match(line) if at_top_level else None
        if header and header.group(1) == "[" and _is_features_table(header.group(2)):
            return True
    return False


# A top-level assignment *to* `features` itself -- an inline table, a dotted key,
# or a scalar. These are the spellings the line rewriter cannot edit, and the
# reason it must not append a `[features]` table beside them. A `[features.sub]`
# header is not one of these: it declares a different table, and appending after
# it is legal.
_ASSIGNS_FEATURES_RE = re.compile(r"""^\s*(?:features|"features"|'features')\s*[.=]""")


def _assigns_features_directly(text: str) -> bool:
    lines = text.splitlines()
    for _index, line, at_top_level in _top_level_lines(lines):
        if not at_top_level:
            continue
        if _ASSIGNS_FEATURES_RE.match(line):
            return True
        # `[[features]]` counts too: an array-of-tables is not a table this can
        # append a `[features]` beside, and letting it fall through produced the
        # "would not be valid TOML" message that this branch exists to replace.
        header = _TABLE_RE.match(line)
        if header and header.group(1) == "[[" and _is_features_table(header.group(2)):
            return True
    return False


def enable_codex_hooks(text: str) -> str | None:
    """Return `text` with `codex_hooks = true` under [features], or None if no change is needed.

    The previous version tested `"codex_hooks = true" in text` and, failing that,
    appended a second assignment to the end of the [features] table. Against an
    existing `codex_hooks = false` that produced a duplicate key, which is not
    valid TOML -- so the installer exited 0 having made the config unloadable.
    The same substring test also read `# codex_hooks = true` in a comment as
    already-enabled, and missed `codex_hooks=true` written without spaces.

    So this tracks which table each line belongs to and rewrites the existing
    assignment in place instead of adding one. Duplicates left behind by the old
    installer are collapsed rather than preserved: someone hitting this bug
    already has a broken file, and refusing to touch it would leave them to hand-
    edit TOML to run an installer.
    """
    # Ask the parser before reaching for the line rewriter. TOML can spell this
    # flag several ways the rewriter below does not read -- an inline table
    # (`features = { codex_hooks = true }`), a dotted key
    # (`features.codex_hooks = true`), a quoted key or header -- and for each of
    # them the rewriter concludes there is no [features] table, appends one, and
    # produces a duplicate declaration. On a config that was already valid and
    # already enabled, that turns a no-op into an abort telling the user to
    # hand-edit TOML. Corrupt input deliberately falls through: repairing it is
    # the reason this function exists.
    if tomllib is not None:
        try:
            features = tomllib.loads(text).get("features")
        except tomllib.TOMLDecodeError:
            pass
        else:
            if isinstance(features, dict) and features.get("codex_hooks") is True:
                return None
            # Already spelled some other way, but not enabled. The rewriter would
            # append a second [features] and the output check would reject it --
            # correct, but under a message about invalid TOML, which points at
            # the wrong thing entirely. The config is fine; this function just
            # cannot edit that form. Say so, with the value it found.
            #
            # Only when the flag itself is unreachable, though. `features` being
            # present is not enough: `[features.sub]` puts a dict there while the
            # key is simply absent, and appending a `[features]` table after a
            # sub-table is legal TOML and is what the old code correctly did. The
            # cases that must stop here are the flag declared somewhere no line
            # edit can reach it, and `features` being a scalar that cannot hold a
            # table at all.
            if (
                features is not None
                and not _has_features_header(text)
                and _assigns_features_directly(text)
            ):
                current = (
                    features.get("codex_hooks")
                    if isinstance(features, dict)
                    else features
                )
                raise SystemExit(
                    f"Cannot enable codex_hooks automatically: `features` is "
                    f"declared in a form this installer does not edit (found "
                    f"codex_hooks = {current!r}).\n"
                    f"Set `codex_hooks = true` under a [features] table by hand "
                    f"and re-run."
                )

    lines = text.splitlines()
    table: str | None = None
    key_lines: list[int] = []
    features_end: int | None = None

    for index, line, at_top_level in _top_level_lines(lines):
        header = _TABLE_RE.match(line) if at_top_level else None
        if header:
            if table == "features":
                features_end = index
            # `[[features]]` is an array-of-tables and a different construct from
            # the `[features]` table, so only the single-bracket form counts.
            table = (
                "features"
                if header.group(1) == "[" and _is_features_table(header.group(2))
                else None
            )
            continue
        if (
            at_top_level
            and table == "features"
            and not _is_comment(line)
            and _CODEX_HOOKS_RE.match(line)
        ):
            key_lines.append(index)

    if table == "features":
        features_end = len(lines)

    if key_lines:
        first = key_lines[0]
        indent = _CODEX_HOOKS_RE.match(lines[first]).group(1)
        if lines[first] == f"{indent}codex_hooks = true" and len(key_lines) == 1:
            return None
        lines[first] = f"{indent}codex_hooks = true"
        # Drop the extras back-to-front so earlier indices stay valid.
        for duplicate in reversed(key_lines[1:]):
            del lines[duplicate]
    elif features_end is not None:
        # [features] exists without the key. Insert at the end of that table, not
        # the end of the file, or it would land under whatever table came next.
        at = features_end
        while at > 0 and not lines[at - 1].strip():
            at -= 1
        lines.insert(at, "codex_hooks = true")
    else:
        if tomllib is None and _MENTIONS_FEATURES_RE.search(text):
            # Appending a table blind is the one branch that can corrupt a config
            # we were unable to read: if `features` is already declared some other
            # way, this produces a duplicate declaration, and with no parser there
            # is nothing to catch it before the write.
            #
            # Gated on the word appearing at all, not on the file being non-empty.
            # A config that never mentions `features` -- which is most of them,
            # Codex writes [projects."..."] and [mcp_servers.*] -- cannot collide
            # with a table that is about to be appended, and refusing those would
            # block the install on every 3.10 machine that has ever run Codex.
            raise SystemExit(
                "Cannot safely enable codex_hooks: this interpreter has neither "
                "tomllib (Python 3.11+) nor tomli, so the existing config cannot "
                "be checked before editing.\n"
                "Run the installer with Python 3.11+, `pip install tomli`, or set "
                "`codex_hooks = true` under a [features] table by hand."
            )
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[features]", "codex_hooks = true"])

    return "\n".join(lines) + "\n"


def ensure_codex_hooks_enabled(config_path: Path) -> None:
    if not config_path.exists():
        install_file(source_codex_dir() / "config.toml", config_path, "copy", force=False)
        return

    # utf-8-sig, not the locale codec. TOML is defined as UTF-8, but read_text()
    # with no encoding uses the ANSI codepage on Windows (cp1252 here), so a
    # non-Latin-1 path in a [projects."..."] header raised UnicodeDecodeError.
    # -sig additionally strips the BOM that PowerShell 5.1's Set-Content writes
    # by default; left in place it hides the first [features] header from the
    # line scan below, which then appends a second one and aborts on the
    # duplicate.
    updated = enable_codex_hooks(config_path.read_text(encoding="utf-8-sig"))
    if updated is None:
        return

    # Never write something the user's TOML parser will reject. Only the *result*
    # has to parse -- requiring the input to would refuse to run on a config the
    # old version of this function already corrupted, which is precisely the file
    # most in need of the repair above.
    if tomllib is not None:
        try:
            tomllib.loads(updated)
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(
                f"Refusing to write {config_path}: the result would not be valid TOML ({exc}).\n"
                f"Set 'codex_hooks = true' under [features] by hand and re-run."
            ) from exc

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(updated, encoding="utf-8")


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


def rewrite_hooks_for_windows(hooks_json: Path, launcher: Path) -> bool:
    """Point every hook command at the interpreter instead of at `/bin/sh`.

    `hooks.json` ships a `/bin/sh -lc '...'` one-liner per hook. Native Windows
    has no `/bin/sh`, so *every* hook fails to launch there — before any of the
    hook's own code runs (ix-infrastructure/Ix#383). The Python half of that
    issue, `subprocess` not finding `ix.CMD` without consulting `PATHEXT`, is
    fixed in `common.py`, and is unreachable until this one is fixed too.

    Windows only. On Unix the installed file stays byte-identical to the source,
    which keeps `install_file`'s content comparison — and therefore re-running
    the installer without `--force` — working exactly as before.

    `sys.executable` is the interpreter running this installer. Baking it in
    means no `python3` has to be on `PATH`, which is its own Windows trap: the
    Ix CLI installs as `ix.CMD` and Python installs as `python.exe`/`py.exe`, and
    `python3` is frequently the Microsoft Store stub that opens a web page.

    Returns True if the file was rewritten.
    """
    if os.name != "nt":
        return False

    try:
        payload = json.loads(hooks_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A hooks.json we cannot parse is not one we should rewrite. Leave it
        # for the user to see rather than replacing it with a guess.
        return False

    changed = False
    for blocks in payload.get("hooks", {}).values():
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

    if changed:
        hooks_json.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
    return changed


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

    # After the hooks are on disk, so the launcher this points at exists.
    # No-op off Windows.
    hooks_json = codex_dir / "hooks.json"
    if os.name == "nt" and hooks_json.is_symlink():
        # A symlinked hooks.json points back at the repo, and rewriting through
        # it would edit the source tree. The rendered file is machine-specific
        # (it names this interpreter's absolute path), so it cannot be shared by
        # a symlink in the first place.
        hooks_json.unlink()
        shutil.copy2(source_codex_dir() / "hooks.json", hooks_json)
    rewrite_hooks_for_windows(hooks_json, hooks_destination / "_launch.py")

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
