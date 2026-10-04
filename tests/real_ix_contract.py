#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""The hooks against a released `ix` CLI -- no fake, no backend.

Every file named test_*.py fakes `ix`. That keeps the main suite hermetic, and it
also means nothing there can notice when Ix renames a flag or changes an output
key: the fake answers whatever the hooks ask. This file is the other half. It is
deliberately not named test_*.py, so `unittest discover` in the ordinary test job
never picks it up; the `real-ix` CI job runs it on its own with a pinned Ix
release first on PATH and the backend unreachable:

    IX_ENDPOINT=http://127.0.0.1:1 IX_HOME=$(mktemp -d) IX_NO_UPDATE_CHECK=1 \\
        python tests/real_ix_contract.py -v

With no backend, every graph command fails -- which is the point. The CLI still
parses each argv in full before it tries the network, so an option it does not
know is rejected as "unknown option" on stderr exactly as it would be for a user,
and the commands that need no backend (`text`, the Pro stub behind `briefing`)
answer for real. The hooks must survive all of it and still speak Codex's hook
protocol.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
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
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO / ".codex" / "hooks"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_codex_hook_protocol import load_schema, validate  # noqa: E402

EXPECTED_VERSION = os.environ.get("IX_EXPECTED_VERSION", "0.12.0")

# What commander prints when it rejects an argv before any action runs.
REJECTED_ARGV = re.compile(
    r"unknown option|unknown command|too many arguments|missing required argument"
    r"|error: option '[^']*' argument missing|is invalid\. Allowed choices",
    re.IGNORECASE,
)

# Every `["ix", "<subcommand>", ...]` literal in the hooks. The argv contract
# below must exercise each one, so a call site added without coverage here fails
# instead of slipping past.
IX_CALL_RE = re.compile(r"""\[\s*["']ix["']\s*,\s*["']([a-z-]+)["']""")


def ix_subcommands_in_hooks() -> set[str]:
    found: set[str] = set()
    for source in HOOKS_DIR.glob("*.py"):
        found |= set(IX_CALL_RE.findall(source.read_text(encoding="utf-8")))
    return found


def load_common(state_dir: Path):
    """A fresh import of the hooks' common.py with its caches in `state_dir`."""
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    sys.modules.pop("common", None)
    spec = importlib.util.spec_from_file_location("common", HOOKS_DIR / "common.py")
    common = importlib.util.module_from_spec(spec)
    sys.modules["common"] = common
    spec.loader.exec_module(common)
    common.CACHE_DIR = state_dir
    common.STATUS_CACHE_PATH = state_dir / "ix-status.json"
    common.PRO_CACHE_PATH = state_dir / "ix-pro.json"
    common.BRIEFING_CACHE_PATH = state_dir / "ix-briefing.txt"
    return common


class RealIxContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ix = shutil.which("ix")
        if cls.ix is None:
            raise unittest.SkipTest("no `ix` on PATH")  # turned into a failure in main()
        endpoint = os.environ.get("IX_ENDPOINT", "")
        if endpoint != "http://127.0.0.1:1":
            raise RuntimeError(
                f"IX_ENDPOINT must be http://127.0.0.1:1 (closed port) for this check, got {endpoint!r}"
            )

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        # A git repository with a file to search, outside $HOME.
        self.repo = self.tmp / "project"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "widget.py").write_text("def frobnicate_widget():\n    return 1\n", encoding="utf-8")
        # Each test gets its own Ix home, so no workspace is registered unless the
        # test itself registers one.
        self.ix_home = self.tmp / "ix-home"
        self.ix_home.mkdir()
        self.env_patch = patch.dict(os.environ, {"HOME": str(self.home), "IX_HOME": str(self.ix_home)})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    # ── 1. it is the real CLI, at the pinned release ────────────────────────

    def test_real_ix_at_the_pinned_version(self) -> None:
        result = subprocess.run([self.ix, "--version"], capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(EXPECTED_VERSION, result.stdout.strip())

    # ── 2. every argv the hooks build is one this CLI accepts ───────────────

    def test_every_hook_argv_is_accepted_by_the_real_cli(self) -> None:
        """Fails when a hook passes a flag or subcommand this `ix` release lacks."""
        common = load_common(self.tmp / "state")
        calls: list[tuple[list[str], int, str]] = []
        real_run = subprocess.run

        def is_ix(argv) -> bool:
            return bool(argv) and Path(str(argv[0])).name in {"ix", "ix.cmd", "ix.CMD"}

        def recording_run(argv, **kwargs):
            result = real_run(argv, **kwargs)
            if is_ix(argv):
                calls.append((list(argv), result.returncode, result.stderr or ""))
            return result

        real_popen = subprocess.Popen

        def synchronous_popen(argv, cwd=None, env=None, **_kwargs):
            # spawn_background_ix_map detaches; run it to completion instead so
            # its argv is checked like the others. With the backend unreachable
            # it fails fast after parsing.
            proc = real_popen(
                argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            _, stderr = proc.communicate(timeout=60)
            calls.append((list(argv), proc.returncode, stderr or ""))
            return proc

        with patch.object(common.subprocess, "run", recording_run):
            common.ix_healthy(self.repo)
            common.probe_pro(self.repo)
            common.build_search_message("frobnicate_widget", self.repo)  # text + locate
            common.build_search_message("frob.*widget", self.repo)  # text only
            common.build_read_message(str(self.repo / "widget.py"), self.repo)
            common.build_write_warning("widget.py", self.repo)
            common.project_is_mapped(self.repo)
        # Separately: subprocess.run itself is built on Popen.
        with patch.object(common.subprocess, "Popen", synchronous_popen):
            self.assertTrue(common.spawn_background_ix_map(self.repo))

        exercised = {argv[1] for argv, _, _ in calls if len(argv) > 1}
        missing = ix_subcommands_in_hooks() - exercised
        self.assertFalse(missing, f"hook call sites not exercised here: {sorted(missing)}")

        rejected = [
            f"{' '.join(['ix', *argv[1:]])}\n    -> {stderr.strip()}"
            for argv, _, stderr in calls
            if REJECTED_ARGV.search(stderr)
        ]
        self.assertFalse(rejected, "ix rejected an argv the hooks build:\n" + "\n".join(rejected))

    # ── 3. ix's real error record is read as "no answer", never as one ──────

    def test_workspace_not_mapped_record_is_not_taken_for_a_result(self) -> None:
        common = load_common(self.tmp / "state")
        argv = ["ix", "impact", "widget.py", "--format", "json"]
        raw = common.run_command(argv, cwd=self.repo, timeout=30)
        self.assertIsNotNone(raw)
        self.assertNotEqual(0, raw.returncode, "an unmapped workspace must fail")
        record = common.parse_json_output(raw.stdout)
        self.assertIsInstance(record, dict, f"expected a JSON error record, got {raw.stdout!r}")
        self.assertEqual("workspace_not_mapped", record.get("error"))
        self.assertIn("message", record)

        self.assertIsNone(common.run_ix_json(argv, cwd=self.repo, timeout=30))
        self.assertIsNone(common.build_write_warning("widget.py", self.repo))
        self.assertIsNone(common.build_read_message(str(self.repo / "widget.py"), self.repo))

    # ── 4. backend down: health fails closed, Pro stub is a definitive "no" ─

    def test_unreachable_backend_and_pro_stub(self) -> None:
        common = load_common(self.tmp / "state")
        self.assertFalse(common.ix_healthy(self.repo))
        self.assertFalse(json.loads(common.STATUS_CACHE_PATH.read_text())["ok"])

        self.assertEqual((False, None), common.probe_pro(self.repo))
        cached = json.loads(common.PRO_CACHE_PATH.read_text())
        # The OSS CLI's `briefing` is the Pro stub; its sentinel text is what
        # makes "not Pro" definitive rather than a retry-soon blip.
        self.assertFalse(cached.get("tentative", False), f"Pro stub not recognised: {cached}")

    # ── 5. the installer, and the hooks exactly as it wires them for Codex ──

    def host_env(self, state: Path) -> dict[str, str]:
        # No `codex` on PATH, as on a CI runner, so `ix mcp install` has one
        # deterministic answer; ix, node, python and git stay reachable.
        path = [
            entry for entry in os.environ.get("PATH", "").split(os.pathsep)
            if entry and not (Path(entry) / "codex").exists()
        ]
        return {**os.environ, "PATH": os.pathsep.join(path), "XDG_STATE_HOME": str(state)}

    def install(self) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "install_codex_integration.py"),
             "--repo", str(self.repo), "--hooks", "--mcp"],
            env=self.host_env(self.tmp / "state-install"),
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return result, json.loads((self.repo / ".codex" / "hooks.json").read_text(encoding="utf-8"))

    def test_installer_reads_real_ix_mcp_install_report(self) -> None:
        """`ix mcp install --host codex --format json` is parsed, not just run."""
        result, _ = self.install()
        self.assertNotIn("Traceback", result.stderr)
        # The codex row of ix's real JSON report says "not-installed"; the
        # installer must have found that row and said so, rather than falling
        # back to "Ran `ix mcp install`" (unparsed) or reporting a failure.
        self.assertIn("the Codex CLI (`codex`) is not on PATH", result.stdout, result.stdout)
        self.assertNotIn("unknown option", result.stdout + result.stderr)

    def run_hook(self, hooks: dict, event: str, payload: dict, state: Path) -> subprocess.CompletedProcess[str]:
        """Run the command hooks.json registers for `event`, the way Codex does."""
        command = hooks["hooks"][event][0]["hooks"][0]["command"]
        return subprocess.run(
            command,
            shell=True,
            input=json.dumps({"cwd": str(self.repo), "session_id": "real-ix", "hook_event_name": event, **payload}),
            cwd=self.repo,
            env=self.host_env(state),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def assert_protocol(self, event: str, result: subprocess.CompletedProcess[str]) -> dict | None:
        self.assertEqual(0, result.returncode, f"{event} hook exited {result.returncode}: {result.stderr}")
        self.assertNotIn("Traceback", result.stderr)
        if not result.stdout.strip():
            return None
        payload = json.loads(result.stdout)
        self.assertEqual([], validate(payload, load_schema(event)), result.stdout)
        return payload

    def test_hooks_exit_cleanly_and_stay_silent_with_backend_down(self) -> None:
        _, hooks = self.install()
        state = self.tmp / "state-down"
        events = {
            "SessionStart": {"source": "startup"},
            "UserPromptSubmit": {"prompt": "hello"},
            "PreToolUse": {"tool_name": "Bash", "tool_input": {"command": "rg frobnicate_widget"}},
            "PostToolUse": {"tool_name": "Bash", "tool_input": {"command": "echo hi > out.txt"}},
        }
        for event, extra in events.items():
            with self.subTest(event=event):
                payload = self.assert_protocol(event, self.run_hook(hooks, event, extra, state))
                self.assertIsNone(payload, f"{event} spoke although ix is not healthy")
        self.assertTrue((state / "ix-codex-plugin" / "ix-status.json").is_file(), "the hooks never ran")
        stop = self.assert_protocol("Stop", self.run_hook(hooks, "Stop", {}, state))
        self.assertEqual({"continue": True}, stop)

    def test_pre_tool_use_summarises_real_ix_text_output(self) -> None:
        """`ix text` needs no backend; its real JSON must reach the model."""
        _, hooks = self.install()
        state = self.tmp / "state-up"
        cache_dir = state / "ix-codex-plugin"
        cache_dir.mkdir(parents=True, mode=0o700)
        # Health is the only thing ix cannot answer without a backend; say yes.
        (cache_dir / "ix-status.json").write_text(json.dumps({"timestamp": time.time(), "ok": True}))
        result = self.run_hook(
            hooks, "PreToolUse",
            {"tool_name": "Bash", "tool_input": {"command": "rg frobnicate_widget"}},
            state,
        )
        payload = self.assert_protocol("PreToolUse", result)
        self.assertIsNotNone(payload, f"no interception; stderr: {result.stderr}")
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("text hits in widget.py", context)


def main() -> int:
    if shutil.which("ix") is None:
        print("real_ix_contract: no `ix` on PATH -- install a released Ix CLI first", file=sys.stderr)
        return 1
    program = unittest.main(exit=False)
    result = program.result
    if result.skipped:
        print(f"real_ix_contract: {len(result.skipped)} skipped; this check must not skip", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
