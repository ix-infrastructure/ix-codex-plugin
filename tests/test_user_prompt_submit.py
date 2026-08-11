#!/usr/bin/env python3
"""How many times the UserPromptSubmit hook runs `ix briefing`, and when.

The Pro probe used to be `ix briefing --help` — free, and safe to call before
anything else. It is now the real command, which on a Pro install is the most
expensive thing the CLI does: seven parallel reads plus a walk that issues two
sequential round-trips per task across up to five plans. That makes the order of
the guards in `main()`, and what happens to the probe's output, load-bearing in
a way they were not before. Neither is visible from common.py alone, so they are
driven end to end here.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".codex" / "hooks"


def _load(name: str, cache_dir: Path):
    """Load a hook module with its caches redirected into a tempdir."""
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    for stale in ("common", name):
        sys.modules.pop(stale, None)

    spec = importlib.util.spec_from_file_location("common", HOOKS_DIR / "common.py")
    common = importlib.util.module_from_spec(spec)
    sys.modules["common"] = common
    spec.loader.exec_module(common)
    common.CACHE_DIR = cache_dir
    common.STATUS_CACHE_PATH = cache_dir / "ix-status.json"
    common.PRO_CACHE_PATH = cache_dir / "ix-pro.json"
    common.BRIEFING_CACHE_PATH = cache_dir / "ix-briefing.txt"

    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return common, module


class UserPromptSubmitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)
        self.common, self.hook = _load("user_prompt_submit", self.cache_dir)

    def _run_hook(self, *, runtime_answers: bool, briefing_stdout: str = '{"plans": []}'):
        """Drive main() once. Returns the argv of every `ix` call it made."""
        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(list(argv))
            out = "ok" if argv[:2] == ["ix", "status"] else briefing_stdout
            return subprocess.CompletedProcess(argv, 0, out, "")

        runtime = {"briefing": "from the runtime API"} if runtime_answers else None
        self.emitted: list[dict] = []
        # ix_available() is a real `shutil.which("ix")`, so without this the
        # whole hook returns at the first guard on any machine that has no CLI
        # installed -- every assertion below then passes or fails on whether the
        # developer happens to have `ix` on PATH rather than on the code.
        with patch.object(self.common, "ix_available", return_value=True), patch.object(
            self.common, "run_command", side_effect=fake_run
        ), patch.object(
            self.hook, "read_event", return_value={"cwd": "."}
        ), patch.object(self.hook, "call_runtime", return_value=runtime), patch.object(
            self.hook, "emit_json", side_effect=self.emitted.append
        ):
            self.hook.main()
        return calls

    def _briefings(self, calls) -> int:
        return sum(1 for c in calls if c[:2] == ["ix", "briefing"])

    def test_the_probe_is_not_run_when_no_briefing_is_due(self) -> None:
        """The expensive guard must sit behind the free one."""
        self.common.mark_briefing_sent()  # nothing is due for BRIEFING_TTL_SECONDS
        calls = self._run_hook(runtime_answers=True)
        self.assertEqual(
            0,
            self._briefings(calls),
            "a full `ix briefing` ran on a prompt that had nothing to say",
        )

    def test_one_briefing_per_prompt_when_the_runtime_api_is_down(self) -> None:
        """The probe's output is the briefing; running it twice is the bug."""
        calls = self._run_hook(runtime_answers=False)
        self.assertEqual(1, self._briefings(calls))
        self.assertTrue(self.emitted, "the briefing should still be emitted")
        self.assertIn(
            '{"plans": []}',
            self.emitted[0]["hookSpecificOutput"]["additionalContext"],
            "the reused output must be the same text the fallback would produce",
        )

    def test_one_briefing_per_prompt_when_the_runtime_api_answers(self) -> None:
        calls = self._run_hook(runtime_answers=True)
        self.assertEqual(1, self._briefings(calls))
        self.assertIn(
            "from the runtime API",
            self.emitted[0]["hookSpecificOutput"]["additionalContext"],
        )

    def test_an_oss_install_emits_nothing_and_stops_after_the_probe(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(list(argv))
            if argv[:2] == ["ix", "status"]:
                return subprocess.CompletedProcess(argv, 0, "ok", "")
            return subprocess.CompletedProcess(
                argv, 1, "", "The 'briefing' command requires Ix Pro.\n"
            )

        emitted: list[dict] = []
        with patch.object(self.common, "ix_available", return_value=True), patch.object(
            self.common, "run_command", side_effect=fake_run
        ), patch.object(
            self.hook, "read_event", return_value={"cwd": "."}
        ), patch.object(self.hook, "call_runtime", return_value=None), patch.object(
            self.hook, "emit_json", side_effect=emitted.append
        ):
            self.hook.main()

        self.assertEqual([], emitted)
        self.assertEqual(1, self._briefings(calls), "must not retry the stub")


if __name__ == "__main__":
    unittest.main(verbosity=2)
