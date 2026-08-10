#!/usr/bin/env python3
"""Every `ix` invocation must reach the executable on Windows too.

The installer puts an `ix.CMD` shim on PATH. `subprocess` on Windows hands the
command to CreateProcess, which — unlike the shell — does not consult PATHEXT,
so a bare "ix" matches no file on disk and the call dies with
`FileNotFoundError: [WinError 2]`. `shutil.which("ix")` DOES apply PATHEXT and
finds the shim, and typing `ix` in PowerShell works, which is exactly why this
presented as "the CLI is fine, only the hooks are broken" (Ix#383).

These tests are deliberately platform-independent: they assert that whatever
`shutil.which` resolves is what reaches `subprocess`, which is the property that
was missing. The Windows-specific part is which name `which` finds, and that is
the standard library's job, not ours.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".codex" / "hooks"


def _load_common():
    spec = importlib.util.spec_from_file_location("ix_common", HOOKS_DIR / "common.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WINDOWS_SHIM = r"C:\Users\Win 10\AppData\Local\ix\bin\ix.CMD"


class IxArgvResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.common = _load_common()
        self.common._ix_executable.cache_clear()

    def test_bare_ix_is_replaced_with_the_resolved_path(self) -> None:
        with patch.object(self.common.shutil, "which", return_value=WINDOWS_SHIM):
            self.assertEqual(
                [WINDOWS_SHIM, "status", "--format", "json"],
                self.common.resolve_ix_argv(["ix", "status", "--format", "json"]),
            )

    def test_run_command_execs_the_resolved_path_not_the_bare_name(self) -> None:
        seen: list[list[str]] = []

        def fake_run(argv, **kwargs):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "{}", "")

        with patch.object(self.common.shutil, "which", return_value=WINDOWS_SHIM), \
             patch.object(self.common.subprocess, "run", side_effect=fake_run):
            self.common.run_command(["ix", "status"])

        self.assertEqual([[WINDOWS_SHIM, "status"]], seen)
        self.assertNotIn("ix", seen[0][:1], "a bare 'ix' is what CreateProcess cannot find")

    def test_background_map_spawns_resolve_too(self) -> None:
        # These are fire-and-forget, so a WinError 2 here is silent — the graph
        # simply stops refreshing and nothing says why.
        seen: list[list[str]] = []

        class FakePopen:
            def __init__(self, argv, **kwargs):
                seen.append(argv)

        with patch.object(self.common.shutil, "which", return_value=WINDOWS_SHIM), \
             patch.object(self.common.subprocess, "Popen", FakePopen):
            self.common.spawn_background_ix_ingest("src/app.py", None)
            self.common.spawn_background_ix_map(None)

        self.assertEqual(
            [[WINDOWS_SHIM, "map", "src/app.py"], [WINDOWS_SHIM, "map"]],
            seen,
        )

    def test_non_ix_commands_are_left_alone(self) -> None:
        with patch.object(self.common.shutil, "which", return_value=WINDOWS_SHIM):
            self.assertEqual(["git", "status"], self.common.resolve_ix_argv(["git", "status"]))
            self.assertEqual([], self.common.resolve_ix_argv([]))

    def test_falls_back_to_the_bare_name_when_ix_is_not_installed(self) -> None:
        # Not our error to invent: let the caller see the ordinary not-found.
        with patch.object(self.common.shutil, "which", return_value=None):
            self.assertEqual(["ix", "status"], self.common.resolve_ix_argv(["ix", "status"]))

    def test_resolution_is_cached(self) -> None:
        calls = []

        def counting_which(name):
            calls.append(name)
            return WINDOWS_SHIM

        with patch.object(self.common.shutil, "which", side_effect=counting_which):
            for _ in range(5):
                self.common.resolve_ix_argv(["ix", "status"])

        self.assertEqual(1, len(calls), "PATH lookup should happen once per process")

    def test_every_hook_argv_starts_with_ix(self) -> None:
        """Guards the assumption resolve_ix_argv relies on.

        If a caller ever builds an argv some other way, it silently skips the
        resolver and regresses on Windows only.
        """
        sources = list(HOOKS_DIR.glob("*.py"))
        self.assertTrue(sources, "no hook sources found")
        offenders = []
        for path in sources:
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if '"ix"' in stripped and "subprocess." in stripped:
                    offenders.append(f"{path.name}:{lineno}")
        self.assertEqual([], offenders, "argv passed straight to subprocess without resolve_ix_argv")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
