#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""The hooks' helpers: one module per concern, all reachable through `common`.

`common.py` used to hold every helper the hooks share, so a change to one
concern (the auto-map rules, say) meant editing the file that also held the
Windows process guard. The helpers now live in `ix_*.py` beside it, and
`common` loads and re-exports them so `from common import ...` in the five
hooks keeps working. These tests hold that arrangement in place: the size
limit that motivated it, the re-export, and the loading rules `common`
documents (its own directory, symlinks resolved, fresh on every load, and a
quiet no-op rather than a traceback when a module is missing).
"""

from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO / ".codex" / "hooks"

# About 300 lines per module is the bar the split was made against; a module
# past it is a sign two concerns have grown back together.
MAX_MODULE_LINES = 300


def _load_common(path: Path = HOOKS_DIR / "common.py", name: str = "common_under_test"):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _defined_names(path: Path) -> set[str]:
    """Top-level names a module defines itself (not the ones it imports)."""
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


class ModuleSizeTest(unittest.TestCase):
    def test_no_hook_module_is_past_the_limit(self) -> None:
        sizes = {
            path.name: len(path.read_text(encoding="utf-8").splitlines())
            for path in sorted(HOOKS_DIR.glob("*.py"))
        }
        too_big = {name: lines for name, lines in sizes.items() if lines > MAX_MODULE_LINES}
        self.assertEqual({}, too_big, f"split these by concern (limit {MAX_MODULE_LINES} lines)")


class ReExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.common = _load_common()

    def test_common_lists_every_helper_module_beside_it(self) -> None:
        shipped = {path.stem for path in HOOKS_DIR.glob("ix_*.py")}
        loaded = {module.__name__ for module in self.common.MODULES}
        self.assertEqual(shipped, loaded, "an ix_*.py common does not load, or one it loads is gone")

    def test_every_helper_is_the_same_object_through_common(self) -> None:
        owners: dict[str, str] = {}
        for module in self.common.MODULES:
            for name in _defined_names(Path(module.__file__)):
                self.assertNotIn(
                    name, owners, f"{name} is defined in both {owners.get(name)} and {module.__name__}"
                )
                owners[name] = module.__name__
                self.assertIs(
                    getattr(module, name),
                    getattr(self.common, name),
                    f"common.{name} is not {module.__name__}.{name}",
                )

    def test_what_the_hooks_import_is_there(self) -> None:
        for hook in ("session_start", "user_prompt_submit", "pre_tool_use", "post_tool_use", "stop"):
            tree = ast.parse((HOOKS_DIR / f"{hook}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "common":
                    for alias in node.names:
                        self.assertTrue(hasattr(self.common, alias.name), f"{hook}: {alias.name}")

    def test_stdlib_modules_stay_reachable_through_common(self) -> None:
        # Tests (and anything else) patch `common.subprocess.run` and the like.
        for name in ("os", "shutil", "subprocess", "json", "time"):
            self.assertIsInstance(getattr(self.common, name), types.ModuleType)


class LoadingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

    def test_helpers_come_from_commons_own_directory(self) -> None:
        common = _load_common()
        for module in common.MODULES:
            self.assertEqual(HOOKS_DIR, Path(module.__file__).resolve().parent, module.__name__)

    def test_each_load_is_fresh(self) -> None:
        """A second `common` shares no state with the first, as when it was one file."""
        first, second = _load_common(), _load_common()
        for a, b in zip(first.MODULES, second.MODULES):
            self.assertIsNot(a, b, a.__name__)
        self.assertIsNot(first._ix_executable, second._ix_executable)
        # Inside one load, a module and the ones that import from it agree.
        by_name = {module.__name__: module for module in second.MODULES}
        self.assertIs(second.run_command, vars(by_name["ix_workspace"])["run_command"])

    def test_a_symlinked_common_loads_the_modules_beside_its_target(self) -> None:
        """`--mode symlink`: links in `.codex/hooks/`, modules in the checkout."""
        link = self.tmp / "common.py"
        try:
            link.symlink_to(HOOKS_DIR / "common.py")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available here")
        common = _load_common(link, "common_via_symlink")
        self.assertTrue(callable(common.request_auto_map))
        for module in common.MODULES:
            self.assertEqual(HOOKS_DIR, Path(module.__file__).resolve().parent)

    def test_a_missing_module_is_a_quiet_no_op_not_a_traceback(self) -> None:
        hooks = self.tmp / "hooks"
        shutil.copytree(HOOKS_DIR, hooks, ignore=shutil.ignore_patterns("__pycache__"))
        (hooks / "ix_summaries.py").unlink()
        env = {**os.environ, "PATH": str(self.tmp / "no-ix-here")}
        result = subprocess.run(
            [sys.executable, str(hooks / "stop.py")],
            input="{}",
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            cwd=self.tmp,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("ix_summaries.py is missing", result.stderr)

    def test_a_complete_copy_runs(self) -> None:
        """The control for the test above: the same copy, nothing removed."""
        hooks = self.tmp / "hooks"
        shutil.copytree(HOOKS_DIR, hooks, ignore=shutil.ignore_patterns("__pycache__"))
        env = {**os.environ, "PATH": str(self.tmp / "no-ix-here")}
        result = subprocess.run(
            [sys.executable, str(hooks / "stop.py")],
            input="{}",
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            cwd=self.tmp,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('{"continue": true}', result.stdout.strip())
        self.assertEqual("", result.stderr)


if __name__ == "__main__":
    unittest.main()
