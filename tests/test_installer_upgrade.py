#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""Re-running the installer is how users upgrade, so it has to just work.

Up to 2.4.3 a re-run after any change -- a new version, an edited hook, a hook
of the user's own in hooks.json -- raised FileExistsError, and the hosted
one-liners never pass --force. By then part of the install had been written.
Even with --force, an `ix-memory` registration of the server.py that 2.4.1 and
earlier shipped was never replaced, because `ix mcp install` ran without
--force, unbounded, with its exit status ignored.

Every run here is the real installer in a subprocess, against a temporary HOME
and CODEX_HOME, with a fake `ix` first on PATH that records its argv and
emulates `ix mcp install --host codex` against the temp config. Nothing here
reads or writes the real ~/.codex.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "scripts" / "install_codex_integration.py"
SHIPPED_HOOKS_JSON = REPO / ".codex" / "hooks.json"
PLUGIN_VERSION = json.loads(
    (REPO / "plugins" / "ix-memory" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
)["version"]

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10
    tomllib = None  # type: ignore[assignment]


# The fake CLI. `--version` answers; `mcp install --host codex` does what the
# real one does to Codex's config: an `ix-memory` entry that is not `ix mcp` is
# a conflict, left alone, unless --force. FAKE_IX_MODE selects a failure.
FAKE_IX = textwrap.dedent(
    r'''
    import json, os, re, sys, time
    from pathlib import Path

    argv = sys.argv[1:]
    with open(os.environ["IX_FAKE_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(argv) + "\n")
    mode = os.environ.get("FAKE_IX_MODE", "ok")

    if argv[:1] == ["--version"]:
        print(os.environ.get("FAKE_IX_VERSION", "0.11.1"))
        sys.exit(0)
    if argv[:2] != ["mcp", "install"]:
        sys.exit(0)

    def report(outcome, code=0):
        print(json.dumps({"hosts": [{"id": "codex", "label": "Codex CLI",
                                     "outcome": outcome}],
                          "registered": int(outcome == "registered"), "conflicts": 0}))
        sys.exit(code)

    if mode == "hang":
        time.sleep(6)
        report("registered")
    if mode == "fail":
        print("codex mcp add exploded", file=sys.stderr)
        report("failed", 1)
    if mode == "noforce" and "--force" in argv:
        print("error: unknown option '--force'", file=sys.stderr)
        sys.exit(1)

    config = Path(os.environ["CODEX_HOME"]) / "config.toml"
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    table = re.compile(r"^\[mcp_servers\.ix-memory\]\n(?:(?!\[).*\n?)*", re.M)
    found = table.search(text)
    if found and 'command = "ix"' in found.group(0) and '"mcp"' in found.group(0):
        report("already-registered")
    if found and "--force" not in argv:
        report("conflict")
    text = table.sub("", text)
    if text and not text.endswith("\n"):
        text += "\n"
    text += '[mcp_servers.ix-memory]\ncommand = "ix"\nargs = ["mcp"]\n'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(text, encoding="utf-8")
    report("registered")
    '''
)


def snapshot(root: Path) -> dict[str, object]:
    """Every path under `root` with its bytes, link target, or 'dir'."""
    state: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[key] = ("link", os.readlink(path))
        elif path.is_dir():
            state[key] = "dir"
        else:
            state[key] = path.read_bytes()
    return state


def old_hooks_json(windows_launcher: str | None = None) -> str:
    """hooks.json as 2.4.2 and earlier shipped it: `-lc`, and `Bash`-only matchers.

    With `windows_launcher`, as the 2.4.x installer rendered it on Windows.
    """
    payload = json.loads(SHIPPED_HOOKS_JSON.read_text(encoding="utf-8"))
    for blocks in payload["hooks"].values():
        for block in blocks:
            if block.get("matcher") == "Bash|apply_patch":
                block["matcher"] = "Bash"
            for hook in block["hooks"]:
                hook["command"] = hook["command"].replace("/bin/sh -c ", "/bin/sh -lc ", 1)
                if windows_launcher:
                    name = re.search(r"hooks/(\w+)\.py", hook["command"]).group(1)
                    hook["command"] = f'"C:\\Python311\\python.exe" "{windows_launcher}" {name}'
    return json.dumps(payload, indent=2) + "\n"


def plugin_handlers(payload: dict) -> list[dict]:
    return [
        hook
        for blocks in payload["hooks"].values()
        for block in blocks
        for hook in block["hooks"]
        if re.search(r"\.codex[/\\]hooks[/\\]\w+\.py", hook.get("command", ""))
    ]


USER_HOOK = {
    "type": "command",
    "command": "/usr/local/bin/my-audit-hook --event pre",
    "statusMessage": "my own hook",
}


class InstallerHarness(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name).resolve()
        self.home = self.root / "home"
        self.codex = self.home / ".codex"
        self.codex.mkdir(parents=True)
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "ix.log"
        self.log.write_text("", encoding="utf-8")
        script = self.root / "fake_ix.py"
        script.write_text(FAKE_IX, encoding="utf-8")
        if os.name == "nt":
            (self.bin / "ix.cmd").write_text(
                f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
            )
        else:
            fake = self.bin / "ix"
            fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            fake.chmod(0o755)

    def env(self, ix: bool = True, **extra: str) -> dict[str, str]:
        # Drop every PATH entry that has a real `ix`, so only the fake answers.
        path = [
            entry
            for entry in os.environ.get("PATH", "").split(os.pathsep)
            if entry and shutil.which("ix", path=entry) is None
        ]
        if ix:
            path.insert(0, str(self.bin))
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(self.home),
                "USERPROFILE": str(self.home),
                "CODEX_HOME": str(self.codex),
                "TMPDIR": str(self.tmp),
                "TMP": str(self.tmp),
                "TEMP": str(self.tmp),
                "XDG_STATE_HOME": str(self.root / "state"),
                "PATH": os.pathsep.join(path),
                "IX_FAKE_LOG": str(self.log),
            }
        )
        env.pop("FAKE_IX_MODE", None)
        env.update(extra)
        return env

    def install(self, *args: str, ix: bool = True, timeout: float = 60, **extra: str):
        argv = list(args) or ["--home"]
        return subprocess.run(
            [sys.executable, str(INSTALLER), *argv],
            capture_output=True,
            text=True,
            env=self.env(ix=ix, **extra),
            timeout=timeout,
        )

    def ok(self, *args: str, **kwargs):
        result = self.install(*args, **kwargs)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def ix_calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def mcp_calls(self) -> list[list[str]]:
        return [call for call in self.ix_calls() if call[:2] == ["mcp", "install"]]

    def hooks_json(self) -> dict:
        return json.loads((self.codex / "hooks.json").read_text(encoding="utf-8-sig"))

    def config(self) -> str:
        path = self.codex / "config.toml"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    # -- a synthesized install from an earlier version ---------------------

    def synthesize(self, version: str, *, legacy_mcp: bool = False, windows: bool = False) -> None:
        hooks = self.codex / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        launcher = str(hooks / "_launch.py") if windows else None
        if version == "2.4.3":
            text = SHIPPED_HOOKS_JSON.read_text(encoding="utf-8")
        else:
            text = old_hooks_json(launcher)
        (self.codex / "hooks.json").write_text(text, encoding="utf-8")
        for source in (REPO / ".codex" / "hooks").glob("*.py"):
            (hooks / source.name).write_text(f"# ix-memory {version} copy of {source.name}\n")
        (self.codex / "ix-plugin-version.json").write_text(
            json.dumps({"plugin_name": "ix-memory", "plugin_version": version,
                        "installed_at": "2026-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        if version != "2.4.3":
            (self.codex / "config.toml").write_text(
                'model = "o3"\n\n[features]\ncodex_hooks = true\n', encoding="utf-8"
            )
        plugin = self.codex / "plugins" / "ix-memory"
        (plugin / ".codex-plugin").mkdir(parents=True)
        (plugin / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "ix-memory", "version": version}), encoding="utf-8"
        )
        # A skill an earlier version shipped and this one dropped.
        (plugin / "skills" / "ix-search").mkdir(parents=True)
        (plugin / "skills" / "ix-search" / "SKILL.md").write_text("old\n", encoding="utf-8")
        market = self.home / ".agents" / "plugins" / "marketplace.json"
        market.parent.mkdir(parents=True)
        market.write_text(json.dumps({
            "name": "ix-codex-plugin",
            "interface": {"displayName": "ix-codex-plugin"},
            "plugins": [
                {"name": "someone-else", "source": {"source": "local", "path": "./x"}},
                {"name": "ix-memory", "version": version,
                 "source": {"source": "local", "path": "./.codex/plugins/ix-memory"}},
            ],
        }, indent=2), encoding="utf-8")
        if legacy_mcp:
            mcp = self.codex / "mcp"
            mcp.mkdir()
            (mcp / "server.py").write_text("# FastMCP server\n", encoding="utf-8")
            (mcp / "ix_llm.py").write_text("# helper\n", encoding="utf-8")
            server = (mcp / "server.py").as_posix()
            with (self.codex / "config.toml").open("a", encoding="utf-8") as fh:
                fh.write(f'\n[mcp_servers.ix-memory]\ncommand = "python3"\nargs = ["{server}"]\n')

    def assert_current_install(self, synthesized: bool = True) -> None:
        hooks = self.codex / "hooks"
        for source in (REPO / ".codex" / "hooks").glob("*.py"):
            self.assertEqual(source.read_bytes(), (hooks / source.name).read_bytes(), source.name)
        plugin = self.codex / "plugins" / "ix-memory"
        self.assertEqual(
            PLUGIN_VERSION,
            json.loads((plugin / ".codex-plugin" / "plugin.json").read_text())["version"],
        )
        self.assertFalse((plugin / "skills" / "ix-search").exists(), "a dropped skill survived")
        handlers = plugin_handlers(self.hooks_json())
        self.assertEqual(5, len(handlers), "plugin hooks duplicated or missing")
        for handler in handlers:
            self.assertNotIn("-lc", handler["command"])
        matchers = [b.get("matcher") for b in self.hooks_json()["hooks"]["PreToolUse"]]
        self.assertIn("Bash|apply_patch", matchers)
        self.assertNotIn("Bash", matchers)
        manifest = json.loads((self.codex / "ix-plugin-version.json").read_text())
        self.assertEqual(PLUGIN_VERSION, manifest["plugin_version"])
        self.assertIn(".codex/hooks/common.py", manifest["files"])
        market = json.loads((self.home / ".agents" / "plugins" / "marketplace.json").read_text())
        names = [p["name"] for p in market["plugins"]]
        self.assertEqual(1, names.count("ix-memory"))
        if synthesized:
            self.assertIn("someone-else", names, "another plugin's entry was dropped")
        entry = next(p for p in market["plugins"] if p["name"] == "ix-memory")
        self.assertEqual(PLUGIN_VERSION, entry["version"])
        self.assertEqual([], list(self.home.rglob("*.ix-new-*")) + list(self.home.rglob("*.ix-old-*")))


class FreshInstall(InstallerHarness):
    def test_fresh_install(self) -> None:
        result = self.ok()
        if os.name == "nt":
            for handler in plugin_handlers(self.hooks_json()):
                self.assertTrue(handler["commandWindows"].startswith("& "))
        else:
            self.assertEqual(SHIPPED_HOOKS_JSON.read_bytes(), (self.codex / "hooks.json").read_bytes())
        self.assertIn("trust the Ix hooks", result.stdout)
        self.assertEqual(
            [["mcp", "install", "--host", "codex", "--format", "json"]], self.mcp_calls()
        )
        self.assertIn('command = "ix"', self.config())
        self.assertIn("Registered `ix mcp`", result.stdout)


class Reinstall(InstallerHarness):
    def test_same_version_with_the_users_own_hook_added(self) -> None:
        """What Codex users do between installs: add a hook of their own."""
        self.ok()
        payload = self.hooks_json()
        payload["hooks"].setdefault("PreToolUse", []).append({"matcher": "Bash", "hooks": [USER_HOOK]})
        (self.codex / "hooks.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        before = snapshot(self.home)

        result = self.ok()
        self.assertEqual(
            {k: v for k, v in before.items() if k != ".codex/config.toml"},
            {k: v for k, v in snapshot(self.home).items() if k != ".codex/config.toml"},
            "a same-version reinstall must change nothing",
        )
        self.assertNotIn("trust the Ix hooks", result.stdout)
        self.assertIn("hooks.json is unchanged", result.stdout)

    def test_a_locally_edited_hook_file_is_restored(self) -> None:
        self.ok()
        (self.codex / "hooks" / "post_tool_use.py").write_text("# edited\n", encoding="utf-8")
        self.ok()
        self.assert_current_install(synthesized=False)


class Upgrade(InstallerHarness):
    def test_from_a_pre_24_install(self) -> None:
        """2.4.1: copied server.py registered by hand, codex_hooks flag, `-lc`, `Bash`."""
        self.synthesize("2.4.1", legacy_mcp=True)
        result = self.ok()
        self.assert_current_install()

        self.assertIn("--force", self.mcp_calls()[0], "the stale server.py entry needs --force")
        self.assertNotIn("server.py", self.config())
        self.assertIn('command = "ix"', self.config())
        self.assertFalse((self.codex / "mcp" / "server.py").exists())
        self.assertFalse((self.codex / "mcp").exists())
        self.assertIn("codex_hooks = true", self.config(), "config.toml is the user's to edit")
        self.assertIn("written by an older installer", result.stdout)
        self.assertIn("trust the Ix hooks", result.stdout)

    def test_from_2_4_2(self) -> None:
        self.synthesize("2.4.2")
        self.ok()
        self.assert_current_install()

    def test_from_2_4_3(self) -> None:
        self.synthesize("2.4.3")
        self.ok()
        self.assert_current_install()
        self.assertNotIn("--force", self.mcp_calls()[0], "nothing stale to replace")

    def test_from_a_2_4_x_windows_install(self) -> None:
        """2.4.x on Windows wrote `"python" "...\\_launch.py" name` as the command."""
        self.synthesize("2.4.2", windows=True)
        self.ok()
        self.assert_current_install()
        for handler in plugin_handlers(self.hooks_json()):
            self.assertNotIn("Python311", handler["command"])

    def test_a_retired_file_in_the_manifest_is_removed_and_a_users_file_is_not(self) -> None:
        self.ok()
        manifest_path = self.codex / "ix-plugin-version.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"].append(".codex/hooks/retired_hook.py")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (self.codex / "hooks" / "retired_hook.py").write_text("# old\n", encoding="utf-8")
        (self.codex / "hooks" / "my_own.py").write_text("# mine\n", encoding="utf-8")

        self.ok()
        self.assertFalse((self.codex / "hooks" / "retired_hook.py").exists())
        self.assertTrue((self.codex / "hooks" / "my_own.py").exists())

    @unittest.skipIf(os.name == "nt", "symlinks need a privilege on Windows")
    def test_from_a_symlink_install_to_copies(self) -> None:
        self.ok("--home", "--plugin", "--hooks", "--mode", "symlink")
        self.assertTrue((self.codex / "hooks" / "common.py").is_symlink())
        self.ok("--home", "--plugin", "--hooks")
        self.assertFalse((self.codex / "hooks" / "common.py").is_symlink())
        self.assertFalse((self.codex / "hooks.json").is_symlink())
        self.assertEqual(SHIPPED_HOOKS_JSON.read_bytes(), (self.codex / "hooks.json").read_bytes())


class UsersOwnHooks(InstallerHarness):
    def test_a_users_hooks_json_is_merged_into_not_replaced(self) -> None:
        mine = {
            "hooks": {
                "PreToolUse": [{"matcher": "Bash", "hooks": [USER_HOOK]}],
                "SessionEnd": [{"hooks": [{"type": "command", "command": "echo bye"}]}],
            }
        }
        (self.codex / "hooks.json").write_text(json.dumps(mine), encoding="utf-8")
        self.ok()
        payload = self.hooks_json()
        self.assertEqual(mine["hooks"]["SessionEnd"], payload["hooks"]["SessionEnd"])
        self.assertEqual(
            {"matcher": "Bash", "hooks": [USER_HOOK]}, payload["hooks"]["PreToolUse"][0],
            "the user's hook must keep its position",
        )
        self.assertEqual(5, len(plugin_handlers(payload)))

        self.ok()
        self.assertEqual(payload, self.hooks_json(), "a second run must not duplicate anything")

    def test_an_old_plugin_entry_beside_the_users_is_replaced_in_place(self) -> None:
        payload = json.loads(old_hooks_json())
        payload["hooks"]["PreToolUse"].insert(0, {"matcher": "Bash", "hooks": [USER_HOOK]})
        (self.codex / "hooks.json").write_text(json.dumps(payload), encoding="utf-8")
        self.ok()
        pre = self.hooks_json()["hooks"]["PreToolUse"]
        self.assertEqual([USER_HOOK], pre[0]["hooks"])
        self.assertEqual("Bash|apply_patch", pre[1]["matcher"])
        self.assertEqual(2, len(pre))


class Mcp(InstallerHarness):
    def test_ix_missing_is_a_warning_and_the_old_server_is_kept(self) -> None:
        """Nothing replaced the registration, so the file it launches must stay."""
        self.synthesize("2.4.1", legacy_mcp=True)
        result = self.ok(ix=False)
        self.assert_current_install()
        self.assertIn("install the Ix CLI", result.stdout)
        self.assertTrue((self.codex / "mcp" / "server.py").exists())
        self.assertEqual([], self.mcp_calls())

    def test_a_failing_ix_mcp_install_is_reported(self) -> None:
        result = self.ok(FAKE_IX_MODE="fail")
        self.assertIn("failed (exit 1)", result.stdout)
        self.assertIn("codex mcp add exploded", result.stdout)
        self.assert_current_install(synthesized=False)

    def test_a_hanging_ix_mcp_install_is_stopped(self) -> None:
        result = self.ok(FAKE_IX_MODE="hang", IX_CODEX_MCP_TIMEOUT="1")
        self.assertIn("did not finish within 1s", result.stdout)
        self.assert_current_install(synthesized=False)

    def test_a_conflicting_server_is_left_alone_without_force(self) -> None:
        (self.codex / "config.toml").write_text(
            '[mcp_servers.ix-memory]\ncommand = "node"\nargs = ["/opt/other/server.js"]\n',
            encoding="utf-8",
        )
        result = self.ok()
        self.assertIn("left alone", result.stdout)
        self.assertIn("/opt/other/server.js", self.config())
        self.ok("--home", "--force")
        self.assertIn('command = "ix"', self.config())

    @unittest.skipIf(tomllib is None, "the fallback proves its edit with tomllib")
    def test_a_cli_without_force_gets_the_entry_rewritten(self) -> None:
        self.synthesize("2.4.1", legacy_mcp=True)
        result = self.ok(FAKE_IX_MODE="noforce")
        parsed = tomllib.loads(self.config())
        entry = parsed["mcp_servers"]["ix-memory"]
        # As `ix mcp install` writes it: the bare name, or on Windows the
        # resolved shim, since nothing there resolves `ix` to `ix.cmd` for Codex.
        if os.name != "nt":
            self.assertEqual("ix", entry["command"])
        self.assertEqual(["mcp"], entry["args"])
        self.assertEqual("o3", parsed["model"], "the rest of config.toml must survive")
        self.assertIn("Replaced the old ix-memory server", result.stdout)


class NoHalfInstalls(InstallerHarness):
    def test_an_unmergeable_hooks_json_stops_the_install_before_any_write(self) -> None:
        (self.codex / "hooks.json").write_text("{ not json", encoding="utf-8")
        before = snapshot(self.home)
        result = self.install()
        self.assertEqual(1, result.returncode)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("not valid JSON", result.stderr)
        self.assertEqual(before, snapshot(self.home), "a failed install must write nothing")

    def test_a_foreign_marketplace_entry_needs_force(self) -> None:
        market = self.home / ".agents" / "plugins" / "marketplace.json"
        market.parent.mkdir(parents=True)
        market.write_text(json.dumps({"plugins": [
            {"name": "ix-memory", "source": {"source": "local", "path": "./dev/ix-memory"}}
        ]}), encoding="utf-8")
        before = snapshot(self.home)
        result = self.install()
        self.assertEqual(1, result.returncode)
        self.assertEqual(before, snapshot(self.home))
        self.ok("--home", "--force")
        self.assertIn("./.codex/plugins/ix-memory", market.read_text())

    def test_a_failure_partway_through_the_swap_is_rolled_back(self) -> None:
        self.synthesize("2.4.2")
        before = snapshot(self.home)

        spec = importlib.util.spec_from_file_location("ix_installer_upgrade", INSTALLER)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)

        calls = {"n": 0}
        real_replace = os.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 5:
                raise OSError("disk went away")
            return real_replace(src, dst)

        env = self.env()
        with patch.dict(os.environ, env, clear=True), \
                patch.object(installer, "_replace", flaky), \
                patch("sys.stdout"), patch("sys.stderr"):
            code = installer.main(["--home", "--plugin", "--hooks"])
        self.assertEqual(1, code)
        self.assertGreater(calls["n"], 5, "the failure was never reached")
        self.assertEqual(before, snapshot(self.home), "the target must be as it was")


class LegacyCache(InstallerHarness):
    def test_the_old_shared_cache_dir_is_removed(self) -> None:
        old = self.tmp / "ix-codex-hooks"
        old.mkdir()
        for name in ("ix-status.json", "ix-pro.json", "ix-briefing.txt", "ix-runtime-health.json"):
            (old / name).write_text("{}", encoding="utf-8")
        result = self.ok()
        self.assertFalse(old.exists())
        self.assertIn("Removed the old hook cache", result.stdout)

    def test_a_dir_with_anything_else_in_it_is_left_alone(self) -> None:
        old = self.tmp / "ix-codex-hooks"
        old.mkdir()
        (old / "ix-status.json").write_text("{}", encoding="utf-8")
        (old / "not-ours.txt").write_text("x", encoding="utf-8")
        self.ok()
        self.assertTrue((old / "not-ours.txt").exists())
        self.assertTrue((old / "ix-status.json").exists())

    @unittest.skipIf(os.name == "nt", "symlinks need a privilege on Windows")
    def test_a_symlink_is_never_followed(self) -> None:
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "ix-status.json").write_text("{}", encoding="utf-8")
        (self.tmp / "ix-codex-hooks").symlink_to(elsewhere)
        self.ok()
        self.assertTrue((elsewhere / "ix-status.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
