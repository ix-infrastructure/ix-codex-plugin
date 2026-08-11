#!/usr/bin/env python3
"""ix-infrastructure/Ix#383, outer half: hooks could not launch on native Windows.

Every `hooks.json` command was a `/bin/sh -lc '...'` one-liner. Windows has no
`/bin/sh`, so the hook process never started — which also made the *inner* half
of that issue (`subprocess` needing `PATHEXT` to find `ix.CMD`, fixed in
`common.py` by #19) unreachable, because the code carrying that fix never ran.

The fix has two halves of its own: `.codex/hooks/_launch.py` does the walk-up
the shell one-liner used to do, and the installer rewrites `hooks.json` on
Windows to invoke it with an absolute interpreter and an absolute path.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


installer = load_module(
    REPO_ROOT / "scripts" / "install_codex_integration.py", "ix_installer"
)
launcher = load_module(REPO_ROOT / ".codex" / "hooks" / "_launch.py", "ix_launch")

SHIPPED_HOOKS_JSON = REPO_ROOT / ".codex" / "hooks.json"


def commands_in(payload: dict) -> list[str]:
    return [
        hook["command"]
        for blocks in payload["hooks"].values()
        for block in blocks
        for hook in block["hooks"]
    ]


class ShippedConfig(unittest.TestCase):
    """The rewrite recovers each hook's name from its command string."""

    def setUp(self) -> None:
        self.payload = json.loads(SHIPPED_HOOKS_JSON.read_text(encoding="utf-8"))

    def test_every_command_names_a_python_hook(self) -> None:
        # If an edit to hooks.json ever stops naming `.codex/hooks/<name>.py`,
        # the Windows rewrite skips that entry silently and the hook stays
        # broken there. Fail here instead.
        commands = commands_in(self.payload)
        self.assertTrue(commands, "hooks.json declares no hooks")
        for command in commands:
            self.assertIsNotNone(installer.HOOK_COMMAND_RE.search(command), command)

    def test_every_named_hook_exists_on_disk(self) -> None:
        for command in commands_in(self.payload):
            name = installer.HOOK_COMMAND_RE.search(command).group("name")
            self.assertTrue((REPO_ROOT / ".codex" / "hooks" / f"{name}.py").is_file())

    def test_the_launcher_ships_alongside_the_hooks(self) -> None:
        # install_hooks() copies `.codex/hooks/*.py`, so the launcher is picked
        # up by the same glob. If it moves, the rewritten commands point at
        # nothing.
        self.assertTrue((REPO_ROOT / ".codex" / "hooks" / "_launch.py").is_file())


class Rewrite(unittest.TestCase):
    def setUp(self) -> None:
        self._os_name = os.name
        self._executable = sys.executable
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.target = self.tmp / "hooks.json"
        self.target.write_text(
            SHIPPED_HOOKS_JSON.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.launch = self.tmp / "_launch.py"

    def tearDown(self) -> None:
        os.name = self._os_name
        sys.executable = self._executable
        self._dir.cleanup()

    def test_is_a_no_op_off_windows(self) -> None:
        # install_file() decides whether a re-install needs --force by comparing
        # contents, so a gratuitous rewrite would make every re-run fail.
        os.name = "posix"
        before = self.target.read_text(encoding="utf-8")
        self.assertFalse(installer.rewrite_hooks_for_windows(self.target, self.launch))
        self.assertEqual(before, self.target.read_text(encoding="utf-8"))

    def test_removes_every_shell_invocation(self) -> None:
        os.name = "nt"
        self.assertTrue(installer.rewrite_hooks_for_windows(self.target, self.launch))
        commands = commands_in(json.loads(self.target.read_text(encoding="utf-8")))
        self.assertTrue(commands)
        for command in commands:
            self.assertNotIn("/bin/sh", command)
            self.assertIn("_launch.py", command)
            self.assertTrue(command.startswith(f'"{sys.executable}"'), command)

    def test_preserves_which_hook_each_event_fires(self) -> None:
        # A rewrite that pointed every event at the wrong handler would be worse
        # than the bug, and would look like it worked.
        source = json.loads(SHIPPED_HOOKS_JSON.read_text(encoding="utf-8"))
        expected = {
            event: [
                installer.HOOK_COMMAND_RE.search(hook["command"]).group("name")
                for block in blocks
                for hook in block["hooks"]
            ]
            for event, blocks in source["hooks"].items()
        }

        os.name = "nt"
        installer.rewrite_hooks_for_windows(self.target, self.launch)
        payload = json.loads(self.target.read_text(encoding="utf-8"))
        actual = {
            event: [
                hook["command"].rsplit(" ", 1)[1]
                for block in blocks
                for hook in block["hooks"]
            ]
            for event, blocks in payload["hooks"].items()
        }
        self.assertEqual(expected, actual)

    def test_is_idempotent(self) -> None:
        os.name = "nt"
        installer.rewrite_hooks_for_windows(self.target, self.launch)
        once = self.target.read_text(encoding="utf-8")
        self.assertFalse(installer.rewrite_hooks_for_windows(self.target, self.launch))
        self.assertEqual(once, self.target.read_text(encoding="utf-8"))

    def test_quotes_paths_containing_spaces(self) -> None:
        """#349 is a live report from a profile at `C:\\Users\\Win 10`."""
        os.name = "nt"
        sys.executable = r"C:\Users\Win 10\Python\python.exe"
        launch = Path(r"C:\Users\Win 10\.codex\hooks\_launch.py")
        installer.rewrite_hooks_for_windows(self.target, launch)
        payload = json.loads(self.target.read_text(encoding="utf-8"))
        command = payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertTrue(command.startswith(r'"C:\Users\Win 10\Python\python.exe" '))
        self.assertIn(f'"{launch}"', command)

    def test_leaves_an_unparseable_file_alone(self) -> None:
        os.name = "nt"
        self.target.write_text("{ not json", encoding="utf-8")
        self.assertFalse(installer.rewrite_hooks_for_windows(self.target, self.launch))
        self.assertEqual("{ not json", self.target.read_text(encoding="utf-8"))


class Launcher(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self._cwd = Path.cwd()
        self._env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        self._argv = sys.argv

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.argv = self._argv
        self._dir.cleanup()

    def set_home(self, path: Path) -> None:
        os.environ["HOME"] = str(path)
        os.environ["USERPROFILE"] = str(path)

    def make_hook(self, root: Path, name: str, body: str = "") -> Path:
        hook = root / ".codex" / "hooks" / f"{name}.py"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(body, encoding="utf-8")
        return hook

    def test_walks_up_from_a_subdirectory(self) -> None:
        hook = self.make_hook(self.tmp, "session_start")
        nested = self.tmp / "a" / "b" / "c"
        nested.mkdir(parents=True)
        self.assertEqual(hook, launcher.find_hook("session_start", nested))

    def test_prefers_the_nearest_project_copy(self) -> None:
        self.set_home(self.tmp / "home")
        self.make_hook(self.tmp / "home", "stop")
        nearer = self.make_hook(self.tmp / "project", "stop")
        self.assertEqual(nearer, launcher.find_hook("stop", self.tmp / "project"))

    def test_falls_back_to_home(self) -> None:
        self.set_home(self.tmp / "home")
        home_hook = self.make_hook(self.tmp / "home", "stop")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.assertEqual(home_hook, launcher.find_hook("stop", elsewhere))

    def test_a_missing_hook_is_not_an_error(self) -> None:
        # A workspace with no Ix hooks installed is a normal state; it must not
        # fail the Codex turn.
        self.set_home(self.tmp / "nowhere")
        os.chdir(self.tmp)
        self.assertEqual(0, launcher.main(["_launch.py", "session_start"]))

    def test_runs_the_hook_as_main(self) -> None:
        self.make_hook(
            self.tmp, "session_start", "import sys\nprint('ran as', __name__)\n"
        )
        os.chdir(self.tmp)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(0, launcher.main(["_launch.py", "session_start"]))
        self.assertIn("ran as __main__", buffer.getvalue())

    def test_propagates_the_hooks_exit_code(self) -> None:
        self.make_hook(self.tmp, "pre_tool_use", "import sys\nsys.exit(3)\n")
        os.chdir(self.tmp)
        self.assertEqual(3, launcher.main(["_launch.py", "pre_tool_use"]))

    def test_hides_itself_from_the_hooks_argv(self) -> None:
        # A hook reading sys.argv must see what `exec python3 <hook>` gave it.
        self.make_hook(self.tmp, "stop", "import sys\nprint('|'.join(sys.argv[1:]))\n")
        os.chdir(self.tmp)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            launcher.main(["_launch.py", "stop", "--flag", "value"])
        self.assertEqual("--flag|value", buffer.getvalue().strip())

    def test_rejects_a_missing_hook_name(self) -> None:
        self.assertEqual(2, launcher.main(["_launch.py"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
