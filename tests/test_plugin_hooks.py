#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""The hooks ship inside the plugin, and `--hooks` steps aside once Codex runs them.

Codex runs `hooks/hooks.json` from its installed copy of a plugin with
$PLUGIN_ROOT set (checked against Codex 0.162.1: `hooks/list` reports them with
`"source": "plugin"`, and a turn runs them with PLUGIN_ROOT pointing at
`$CODEX_HOME/plugins/cache/<marketplace>/ix-memory/<version>`). These tests
cover what this repo controls: the file itself, running every hook from a bare
copy of the plugin directory, the installer taking its own entries out of
`~/.codex/hooks.json` when the bundled ones are live, and a second delivery of
one event being dropped when both copies are still registered.
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
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_installer_upgrade import USER_HOOK, InstallerHarness, plugin_handlers  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugins" / "ix-memory"
PLUGIN_HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
LEGACY_HOOKS_JSON = REPO / ".codex" / "hooks.json"
PLUGIN_VERSION = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))[
    "version"
]
EVENTS = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "Stop": "stop",
}


def _handlers(path: Path) -> dict[str, tuple[object, dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    found = {}
    for event, groups in payload["hooks"].items():
        assert len(groups) == 1 and len(groups[0]["hooks"]) == 1, event
        found[event] = (groups[0].get("matcher"), groups[0]["hooks"][0])
    return found


class PluginHooksJsonTest(unittest.TestCase):
    def test_the_same_five_hooks_as_the_hooks_json_install(self) -> None:
        bundled, legacy = _handlers(PLUGIN_HOOKS_JSON), _handlers(LEGACY_HOOKS_JSON)
        self.assertEqual(set(EVENTS), set(bundled))
        self.assertEqual(set(legacy), set(bundled))
        for event, (matcher, hook) in bundled.items():
            old_matcher, old = legacy[event]
            self.assertEqual(old_matcher, matcher, event)
            for key in ("type", "timeout", "statusMessage"):
                self.assertEqual(old.get(key), hook.get(key), f"{event} {key}")

    def test_every_command_runs_a_script_inside_the_plugin(self) -> None:
        for event, (_, hook) in _handlers(PLUGIN_HOOKS_JSON).items():
            name = EVENTS[event]
            self.assertTrue((PLUGIN / "hooks" / f"{name}.py").is_file(), name)
            self.assertEqual(f'python3 "$PLUGIN_ROOT/hooks/{name}.py"', hook["command"])
            windows = hook["commandWindows"]
            # PowerShell: `$env:` reads the variable, `&` makes a quoted path a call.
            self.assertIn(f'& py -3 "$env:PLUGIN_ROOT\\hooks\\{name}.py"', windows)
            self.assertIn(f'& python "$env:PLUGIN_ROOT\\hooks\\{name}.py"', windows)
            self.assertNotIn(".codex", hook["command"] + windows)

    def test_the_manifest_does_not_point_codex_somewhere_else(self) -> None:
        # Codex reads hooks/hooks.json by default; a `hooks` entry would replace it.
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertNotIn("hooks", manifest)


def _fake_ix(bin_dir: Path) -> None:
    """An `ix` that answers every call with exit 0 and no output: healthy, empty graph."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (bin_dir / "ix.cmd").write_text("@exit /b 0\r\n", encoding="utf-8")
    else:
        fake = bin_dir / "ix"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)


@unittest.skipIf(os.name == "nt", "runs the POSIX `command`; Windows uses commandWindows")
class PluginOnlyRunTest(unittest.TestCase):
    """Each hook, run the way Codex runs it, from a bare copy of the plugin.

    No `.codex/` anywhere: not in the project, not in HOME. Only the plugin
    directory, as Codex copies it into its plugin cache.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.plugin = self.root / "cache" / "ix-codex-plugin" / "ix-memory" / PLUGIN_VERSION
        shutil.copytree(PLUGIN, self.plugin, ignore=shutil.ignore_patterns("__pycache__"))
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        _fake_ix(self.root / "bin")
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.root / "state"),
            "PLUGIN_ROOT": str(self.plugin),
            "PATH": os.pathsep.join([str(self.root / "bin"), os.environ.get("PATH", "")]),
        }

    def run_hook(self, event: str, extra: dict) -> subprocess.CompletedProcess[str]:
        _, hook = _handlers(self.plugin / "hooks" / "hooks.json")[event]
        payload = {
            "session_id": "plugin-only",
            "turn_id": f"turn-{event}",
            "hook_event_name": event,
            "cwd": str(self.project),
            **extra,
        }
        return subprocess.run(
            ["/bin/sh", "-c", hook["command"]],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=self.project,
            env=self.env,
            timeout=60,
        )

    def test_all_five_hooks_run_from_the_plugin_alone(self) -> None:
        cases = {
            "SessionStart": {"source": "startup"},
            "UserPromptSubmit": {"prompt": "where is main defined?"},
            "PreToolUse": {"tool_name": "Bash", "tool_input": {"command": "rg -n main_loop"}},
            "PostToolUse": {
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi > notes.txt"},
                "tool_response": "",
            },
            "Stop": {"stop_hook_active": False},
        }
        outputs = {}
        for event, extra in cases.items():
            result = self.run_hook(event, extra)
            self.assertEqual(0, result.returncode, f"{event}: {result.stderr}")
            self.assertNotIn("Traceback", result.stderr, event)
            self.assertNotIn("is missing from", result.stderr, event)
            outputs[event] = json.loads(result.stdout) if result.stdout.strip() else None

        session = outputs["SessionStart"]["hookSpecificOutput"]
        self.assertEqual("SessionStart", session["hookEventName"])
        self.assertIn("Ix Memory is available", session["additionalContext"])
        # The version line comes from the plugin manifest: there is no installer
        # version file beside a copy Codex made.
        self.assertIn(f"ix-memory plugin v{PLUGIN_VERSION} active", session["additionalContext"])
        self.assertEqual({"continue": True}, outputs["Stop"])
        self.assertFalse((self.home / ".codex").exists())


@unittest.skipIf(os.name == "nt", "drives the hooks through /bin/sh")
class OneRunPerEventTest(unittest.TestCase):
    """Both copies registered: Codex hands each the same bytes, one of them runs."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.env = {
            **os.environ,
            "XDG_STATE_HOME": str(self.root / "state"),
            "PATH": os.pathsep.join(
                p for p in os.environ.get("PATH", "").split(os.pathsep) if shutil.which("ix", path=p) is None
            ),
        }

    def start_stop_hook(self, payload: str) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [sys.executable, str(PLUGIN / "hooks" / "stop.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.root,
            env=self.env,
        )
        process.stdin.write(payload)
        process.stdin.close()
        return process

    def outputs(self, *payloads: str) -> list[str]:
        processes = [self.start_stop_hook(payload) for payload in payloads]
        results = []
        for process in processes:
            out = process.stdout.read()
            err = process.stderr.read()
            process.wait(timeout=60)
            process.stdout.close()
            process.stderr.close()
            self.assertEqual(0, process.returncode, err)
            self.assertEqual("", err)
            results.append(out.strip())
        return results

    def event(self, **fields: object) -> str:
        return json.dumps({"session_id": "s1", "turn_id": "t1", "hook_event_name": "Stop",
                           "cwd": str(self.root), **fields})

    def test_two_copies_started_together_answer_once(self) -> None:
        payload = self.event()
        results = self.outputs(payload, payload)
        self.assertEqual(['{"continue": true}', ""], sorted(results, reverse=True))

    def test_the_next_event_still_runs(self) -> None:
        self.assertEqual(['{"continue": true}'], self.outputs(self.event()))
        self.assertEqual([""], self.outputs(self.event()))
        self.assertEqual(['{"continue": true}'], self.outputs(self.event(turn_id="t2")))

    def test_input_that_codex_did_not_send_is_never_dropped(self) -> None:
        payload = json.dumps({"cwd": str(self.root)})  # no session or event name
        self.assertEqual(['{"continue": true}'] * 2, self.outputs(payload, payload))


class OnceModuleTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        spec = importlib.util.spec_from_file_location("ix_common_once", PLUGIN / "hooks" / "common.py")
        self.common = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.common)
        from hook_namespace import point_caches_at

        self.cache = Path(tmp.name)
        point_caches_at(self.common, self.cache)

    def test_a_stale_claim_is_taken_over(self) -> None:
        once = self.common.first_delivery
        self.assertTrue(once("event"))
        self.assertFalse(once("event"))
        marker = next(p for p in (self.cache / "events").iterdir() if not p.name.startswith("."))
        old = time.time() - 3600
        os.utime(marker, (old, old))
        self.assertTrue(once("event"))
        self.assertFalse(once("event"))

    def test_an_unusable_state_directory_runs_every_copy(self) -> None:
        (self.cache / "events").write_text("not a directory", encoding="utf-8")
        self.assertTrue(self.common.first_delivery("event"))
        self.assertTrue(self.common.first_delivery("event"))


FAKE_CODEX = '#!/bin/sh\necho "codex-cli ${FAKE_CODEX_VERSION:-0.162.1}"\n'


class InstallerRetiresHooksJsonTest(InstallerHarness):
    def setUp(self) -> None:
        super().setUp()
        if os.name == "nt":
            (self.bin / "codex.cmd").write_text(
                "@echo codex-cli %FAKE_CODEX_VERSION%\r\n", encoding="utf-8"
            )
        else:
            fake = self.bin / "codex"
            fake.write_text(FAKE_CODEX, encoding="utf-8")
            fake.chmod(0o755)

    def ok(self, *args: str, **kwargs):  # type: ignore[override]
        kwargs.setdefault("FAKE_CODEX_VERSION", "0.162.1")
        return super().ok(*args, **kwargs)

    def install_plugin_in_codex(self, marketplace: str = "ix-codex-plugin", hooks: bool = True) -> None:
        """What `codex plugin add ix-memory@<marketplace>` leaves behind."""
        config = self.codex / "config.toml"
        text = config.read_text(encoding="utf-8") if config.exists() else ""
        config.write_text(
            text + f'\n[plugins."ix-memory@{marketplace}"]\nenabled = true\n', encoding="utf-8"
        )
        copy = self.codex / "plugins" / "cache" / marketplace / "ix-memory" / PLUGIN_VERSION
        shutil.copytree(PLUGIN, copy, ignore=shutil.ignore_patterns("__pycache__"))
        if not hooks:
            shutil.rmtree(copy / "hooks")

    def add_user_hook(self) -> None:
        payload = self.hooks_json()
        payload["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [USER_HOOK]})
        (self.codex / "hooks.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def installed_scripts(self) -> list[str]:
        hooks = self.codex / "hooks"
        return sorted(p.name for p in hooks.glob("*.py")) if hooks.exists() else []

    def test_without_the_plugin_in_codex_hooks_json_is_installed_as_before(self) -> None:
        result = self.ok("--home")
        self.assertEqual(5, len(plugin_handlers(self.hooks_json())))
        self.assertIn("common.py", self.installed_scripts())
        self.assertNotIn("bundled", result.stdout)

    def test_once_codex_runs_the_bundled_hooks_the_entries_go(self) -> None:
        self.ok("--home")
        self.add_user_hook()
        (self.codex / "hooks" / "my_own.py").write_text("# mine\n", encoding="utf-8")
        self.install_plugin_in_codex()

        result = self.ok("--home")
        self.assertIn("runs the hooks bundled in ix-memory@ix-codex-plugin", result.stdout)
        payload = self.hooks_json()
        self.assertEqual([], plugin_handlers(payload))
        self.assertEqual([USER_HOOK], [h for g in payload["hooks"]["PreToolUse"] for h in g["hooks"]])
        self.assertEqual(["my_own.py"], self.installed_scripts())
        manifest = json.loads((self.codex / "ix-plugin-version.json").read_text(encoding="utf-8"))
        self.assertEqual([], manifest["files"])
        # The plugin copy the installer manages still carries the hooks.
        self.assertTrue((self.codex / "plugins" / "ix-memory" / "hooks" / "hooks.json").is_file())

        again = self.ok("--home")
        self.assertIn("Everything was already up to date.", again.stdout)

    def test_a_hooks_json_with_only_the_plugin_entries_is_removed(self) -> None:
        self.ok("--home")
        self.install_plugin_in_codex()
        self.ok("--home")
        self.assertFalse((self.codex / "hooks.json").exists())
        self.assertEqual([], self.installed_scripts())

    def test_the_entries_stay_while_codex_cannot_run_the_bundled_hooks(self) -> None:
        cases = {
            "old codex": ({"FAKE_CODEX_VERSION": "0.154.9"}, {}),
            "copy without hooks": ({}, {"hooks": False}),
        }
        for label, (env, plugin) in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.codex)
                self.codex.mkdir()
                self.ok("--home")
                self.install_plugin_in_codex(**plugin)
                result = self.ok("--home", **env)
                self.assertEqual(5, len(plugin_handlers(self.hooks_json())), result.stdout)
                self.assertIn("common.py", self.installed_scripts())

    def test_no_codex_on_path_keeps_the_entries(self) -> None:
        self.ok("--home")
        self.install_plugin_in_codex()
        env = self.env()
        env["PATH"] = os.pathsep.join(
            p for p in env["PATH"].split(os.pathsep) if shutil.which("codex", path=p) is None
        )
        result = subprocess.run(
            [sys.executable, str(sys.modules["test_installer_upgrade"].INSTALLER), "--home"],
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(5, len(plugin_handlers(self.hooks_json())))

    def test_a_repo_install_keeps_its_shared_hooks_json(self) -> None:
        self.install_plugin_in_codex()
        repo = self.root / "repo"
        repo.mkdir()
        self.ok("--repo", str(repo), "--hooks")
        payload = json.loads((repo / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(5, len(plugin_handlers(payload)))


class TreeCopySkipsBytecodeTest(unittest.TestCase):
    def test_pycache_is_not_copied_into_the_plugin(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "installer_tree", REPO / "scripts" / "install_codex_integration.py"
        )
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            (source / "hooks" / "__pycache__").mkdir(parents=True)
            (source / "hooks" / "common.py").write_text("# x\n", encoding="utf-8")
            (source / "hooks" / "__pycache__" / "common.cpython-311.pyc").write_bytes(b"\0")
            (source / "hooks" / "stray.pyc").write_bytes(b"\0")
            tx = installer.Transaction()
            tx.tree(Path(tmp) / "dest", source, "copy")
            tx.commit()
            copied = sorted(
                p.relative_to(Path(tmp) / "dest").as_posix()
                for p in (Path(tmp) / "dest").rglob("*")
                if p.is_file()
            )
        self.assertEqual(["hooks/common.py"], copied)


if __name__ == "__main__":
    unittest.main()
