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

import ast
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".codex" / "hooks"
# Scanned by the argv guard too: the installer spawns `ix` as well, and the
# guard covering only the hooks is exactly how the --mcp path came to spawn
# the bare name.
INSTALLER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "install_codex_integration.py"


def _load_common():
    spec = importlib.util.spec_from_file_location("ix_common", HOOKS_DIR / "common.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A resolved `.CMD` shim, spelled absolutely for whichever platform is running.
# It has to be genuinely absolute, not merely Windows-shaped: `_ix_executable`
# discards a relative resolution (that is the current-directory hijack guard),
# and `C:\...` is not absolute under POSIX rules, so a hardcoded Windows path
# would be thrown away on Linux and every assertion below would then be checking
# the unresolved fallback instead. The `.CMD` suffix is the part that matters —
# it is what routes through cmd.exe.
WINDOWS_SHIM = (
    r"C:\Users\Win 10\AppData\Local\ix\bin\ix.CMD"
    if os.name == "nt"
    else "/opt/ix bin/ix.CMD"
)


def load_installer():
    spec = importlib.util.spec_from_file_location("ix_installer_argv", INSTALLER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ix_installer_argv"] = module
    spec.loader.exec_module(module)
    return module


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

    # ── the cost of resolving ────────────────────────────────────────────────
    # Resolving is what makes the hooks work on Windows and also what makes this
    # reachable: `ix.CMD` is a batch file, CreateProcess runs those through
    # `cmd.exe /c`, and subprocess quotes an argument only when it contains
    # whitespace. Before resolution a bare "ix" could not launch at all there.

    def test_a_working_directory_ix_is_never_resolved(self) -> None:
        """Resolution must not buy PATHEXT at the cost of the current directory.

        On Windows shutil.which searches the CWD first unless
        NoDefaultCurrentDirectoryInExePath is set, which by default it is not --
        so a repo committing `ix.bat` at its root would be run by every hook on
        open. A bare "ix" is immune (CreateProcess only appends `.exe`), so this
        would be a hole resolution *created*. A PATH hit is absolute.
        """
        for relative in (r".\ix.BAT", "ix.CMD", r"sub\ix.bat"):
            with self.subTest(relative):
                with patch.object(
                    self.common.shutil, "which", return_value=relative
                ):
                    self.common._ix_executable.cache_clear()
                    self.assertEqual(
                        ["ix", "status"],
                        self.common.resolve_ix_argv(["ix", "status"]),
                        "a relative resolution must be discarded, not executed",
                    )
        # Control: an absolute hit is still used, or the above passes vacuously.
        with patch.object(self.common.shutil, "which", return_value=WINDOWS_SHIM):
            self.common._ix_executable.cache_clear()
            self.assertEqual(
                [WINDOWS_SHIM, "status"], self.common.resolve_ix_argv(["ix", "status"])
            )

    def test_the_executable_itself_is_scanned(self) -> None:
        """list2cmdline leaves an unquoted path alone, so `C:\\a&b\\ix.cmd` splits."""
        self.assertNotEqual(
            "", self.common.unsafe_for_cmd_shim([r"C:\a&b\ix.cmd", "status"])
        )

    def test_a_shell_metacharacter_stops_the_call(self) -> None:
        for argument in (
            "a&whoami",
            "a|whoami",
            "a>out",
            "a<in",
            "a^b",
            'a"b',
            "%USERNAME%",
            "a!V!",
            "a\rwhoami",
            "a\nwhoami",
        ):
            with self.subTest(argument):
                with patch.object(
                    self.common.shutil, "which", return_value=WINDOWS_SHIM
                ), patch.object(self.common.subprocess, "run") as run:
                    self.common._ix_executable.cache_clear()
                    result = self.common.run_command(["ix", "map", argument])
                self.assertIsNone(result)
                run.assert_not_called()

    def test_the_background_ingest_stops_too(self) -> None:
        """The most exposed argument: a model-written path, sent unattended."""
        with patch.object(
            self.common.shutil, "which", return_value=WINDOWS_SHIM
        ), patch.object(self.common.subprocess, "Popen") as popen:
            self.common._ix_executable.cache_clear()
            self.common.spawn_background_ix_ingest("a&whoami", None)
            popen.assert_not_called()

            self.common.spawn_background_ix_ingest("src/app.py", None)
            popen.assert_called_once()

    def test_a_posix_path_is_never_refused(self) -> None:
        """No cmd.exe, no shim, nothing to refuse — the guard must not fire.

        `&` and `|` are legal in a POSIX filename, and this runs on every call.
        """
        for resolved in ("/usr/local/bin/ix", "/home/a&b/.local/bin/ix"):
            with self.subTest(resolved):
                self.assertEqual(
                    "", self.common.unsafe_for_cmd_shim([resolved, "map", "a&b.py"])
                )

    def test_a_windows_shim_with_a_clean_argument_is_not_refused(self) -> None:
        self.assertEqual(
            "", self.common.unsafe_for_cmd_shim([WINDOWS_SHIM, "map", "src/app.py"])
        )

    def test_every_hook_argv_starts_with_ix(self) -> None:
        """Guards the assumption resolve_ix_argv relies on.

        If a caller ever builds an argv some other way, it silently skips the
        resolver and regresses on Windows only.
        """
        sources = list(HOOKS_DIR.glob("*.py")) + [INSTALLER_PATH]
        self.assertTrue(sources, "no hook sources found")
        offenders = []
        for path in sources:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                # The real shape, not a line-grep. Grepping for `"ix"` and
                # `subprocess.` on one line reads prose as code — a docstring
                # explaining that `subprocess.run(["ix", ...])` is wrong scored as
                # an offender — and reads code as prose the moment a call is split
                # across lines, which is the direction that actually costs
                # something.
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                    continue
                if func.value.id != "subprocess":
                    continue
                if not node.args:
                    continue
                argv = node.args[0]
                if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
                    continue
                head = argv.elts[0]
                if isinstance(head, ast.Constant) and head.value == "ix":
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual([], offenders, "argv passed straight to subprocess without resolve_ix_argv")


class InstallerIxLaunchTest(unittest.TestCase):
    """`--mcp` registers by running the CLI, so it has to reach it on Windows.

    The registration itself is right to delegate — `ix mcp install` resolves the
    launcher for each host it writes. But reaching *that* has the same problem one
    level up: a bare "ix" dies in CreateProcess before `check=False` is consulted,
    so the flag prints its banner and then raises, having registered nothing.
    """

    def setUp(self) -> None:
        self.installer = load_installer()

    def _run_mcp(self, which_returns: str | None, version_stdout: str = "ix 0.9.3"):
        """Drive `main()` down the --mcp path, returning the spawned argvs."""
        calls: list[list[str]] = []

        def fake_run(argv, *args, **kwargs):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout=version_stdout, stderr="")

        with tempfile.TemporaryDirectory() as target:
            argv = ["install_codex_integration.py", "--repo", target, "--mcp"]
            with patch.object(self.installer.shutil, "which", return_value=which_returns), \
                    patch.object(self.installer.subprocess, "run", fake_run), \
                    patch.object(sys, "argv", argv), \
                    redirect_stdout(io.StringIO()) as out:
                self.installer.main()
        return calls, out.getvalue()

    def test_registers_with_the_resolved_path_not_the_bare_name(self) -> None:
        calls, _ = self._run_mcp(WINDOWS_SHIM)
        register = [c for c in calls if "mcp" in c]
        self.assertEqual(1, len(register), f"expected one registration, got {calls}")
        self.assertEqual(
            [WINDOWS_SHIM, "mcp", "install", "--host", "codex"],
            register[0],
            "the bare name never resolves through CreateProcess on Windows",
        )

    def test_the_version_gate_and_the_registration_use_one_install(self) -> None:
        """Two resolutions could gate on one `ix` and then register with another."""
        calls, _ = self._run_mcp(WINDOWS_SHIM)
        self.assertTrue(calls, "nothing was spawned")
        self.assertEqual({WINDOWS_SHIM}, {c[0] for c in calls})

    def test_says_so_rather_than_raising_when_ix_is_not_on_path(self) -> None:
        calls, printed = self._run_mcp(None)
        self.assertEqual([], [c for c in calls if "mcp" in c])
        self.assertIn("install the Ix CLI", printed)

    def test_refuses_a_cli_below_the_floor(self) -> None:
        calls, printed = self._run_mcp(WINDOWS_SHIM, version_stdout="ix 0.9.2")
        self.assertEqual([], [c for c in calls if "mcp" in c])
        self.assertIn("0.9.3", printed)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
