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
import subprocess
import shutil
import sys
import tempfile
import unittest
import uuid
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
        # Under .codex/hooks/, as a real install has it: HOOK_COMMAND_RE looks
        # for that path, so a launcher parked anywhere else makes the second
        # pass match nothing and the idempotency test pass for free.
        self.launch = self.tmp / ".codex" / "hooks" / "_launch.py"

    def tearDown(self) -> None:
        os.name = self._os_name
        sys.executable = self._executable
        self._dir.cleanup()

    def render(self):
        return installer.render_hooks_json(self.target, self.launch)

    def test_is_a_no_op_off_windows(self) -> None:
        # None means "install the source unchanged", which keeps install_file's
        # content comparison — and re-running without --force — working.
        os.name = "posix"
        before = self.target.read_text(encoding="utf-8")
        self.assertIsNone(self.render())
        self.assertEqual(before, self.target.read_text(encoding="utf-8"))

    def test_removes_every_shell_invocation(self) -> None:
        os.name = "nt"
        rendered = self.render()
        self.assertIsNotNone(rendered)
        commands = commands_in(json.loads(rendered))
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
        payload = json.loads(self.render())
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
        once = self.render()
        self.assertIsNotNone(once)
        # Feed the rendered output back in: a second pass must find nothing to do.
        self.target.write_text(once, encoding="utf-8")
        self.assertIsNone(
            self.render(), "the `/bin/sh` guard is what stops a self-referential command"
        )

    def test_quotes_paths_containing_spaces(self) -> None:
        """#349 is a live report from a profile at `C:\\Users\\Win 10`."""
        os.name = "nt"
        sys.executable = r"C:\Users\Win 10\Python\python.exe"
        launch = Path(r"C:\Users\Win 10\.codex\hooks\_launch.py")
        payload = json.loads(installer.render_hooks_json(self.target, launch))
        command = payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertTrue(command.startswith(r'"C:\Users\Win 10\Python\python.exe" '))
        self.assertIn(f'"{launch}"', command)

    def test_leaves_an_unparseable_file_alone(self) -> None:
        os.name = "nt"
        self.target.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(self.render())
        self.assertEqual("{ not json", self.target.read_text(encoding="utf-8"))

    def test_a_shape_we_do_not_understand_is_left_alone(self) -> None:
        """Valid JSON, unexpected structure — must not abort the installer.

        These escaped the JSONDecodeError guard and came out as AttributeError,
        KeyError or TypeError, which nothing catches: a raw traceback partway
        through an install.
        """
        os.name = "nt"
        for body in ("[]", '{"hooks":"nope"}', '{"other":1}',
                     '{"hooks":{"S":[{"hooks":[{"command":5}]}]}}'):
            with self.subTest(body):
                self.target.write_text(body, encoding="utf-8")
                self.assertIsNone(self.render())

    def test_a_byte_order_mark_does_not_skip_the_rewrite(self) -> None:
        """PowerShell 5.1's Set-Content writes one by default.

        Reading as plain utf-8 made json.loads raise, so the rewrite was skipped
        and the installer reported success with all five hooks still dead — the
        exact failure this exists to remove.
        """
        os.name = "nt"
        self.target.write_text(
            SHIPPED_HOOKS_JSON.read_text(encoding="utf-8"), encoding="utf-8-sig"
        )
        rendered = self.render()
        self.assertIsNotNone(rendered)
        for command in commands_in(json.loads(rendered)):
            self.assertNotIn("/bin/sh", command)


class InstallHooksEndToEnd(unittest.TestCase):
    """The wiring, not the pure function.

    Every assertion above passed with the rewrite call deleted from
    install_hooks entirely — the function was covered, its only caller was not.
    These run the real installer into a temp target.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.target = Path(self._dir.name) / "workspace"
        self.target.mkdir()

    def install(self):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "install_codex_integration.py"),
             "--repo", str(self.target), "--hooks"],
            capture_output=True, text=True,
        )

    def installed_commands(self) -> list[str]:
        text = (self.target / ".codex" / "hooks.json").read_text(encoding="utf-8-sig")
        return commands_in(json.loads(text))

    def test_a_second_install_is_not_an_error(self) -> None:
        """The documented Windows one-liner never passes --force.

        Rewriting after install_file left the installed file permanently
        different from the source, so the next run's content comparison raised
        FileExistsError — after install_plugin had written and before install_mcp
        ran, i.e. a partial install with a traceback.
        """
        first = self.install()
        self.assertEqual(0, first.returncode, first.stderr)
        before = (self.target / ".codex" / "hooks.json").read_bytes()

        second = self.install()
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(
            before,
            (self.target / ".codex" / "hooks.json").read_bytes(),
            "a re-install must leave hooks.json byte-identical",
        )

    def test_installing_a_checkout_into_itself_leaves_it_unmodified(self) -> None:
        """`--repo <the plugin checkout>` makes source and destination the same file.

        The rendered command names this machine's interpreter, so writing it
        there would leave the tracked hooks.json modified and every later diff
        carrying one developer's absolute paths.
        """
        checkout = Path(self._dir.name) / "checkout"
        (checkout / "scripts").mkdir(parents=True)
        shutil.copytree(REPO_ROOT / ".codex", checkout / ".codex")
        shutil.copy2(
            REPO_ROOT / "scripts" / "install_codex_integration.py",
            checkout / "scripts" / "install_codex_integration.py",
        )
        before = (checkout / ".codex" / "hooks.json").read_bytes()

        result = subprocess.run(
            [sys.executable, str(checkout / "scripts" / "install_codex_integration.py"),
             "--repo", str(checkout), "--hooks"],
            capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            before,
            (checkout / ".codex" / "hooks.json").read_bytes(),
            "the installer rewrote its own tracked source",
        )

    def test_the_launcher_is_installed_beside_the_hooks(self) -> None:
        self.assertEqual(0, self.install().returncode)
        self.assertTrue((self.target / ".codex" / "hooks" / "_launch.py").is_file())

    @unittest.skipUnless(os.name == "nt", "the rewrite is Windows-only")
    def test_no_hook_still_needs_bin_sh(self) -> None:
        self.assertEqual(0, self.install().returncode)
        commands = self.installed_commands()
        self.assertTrue(commands)
        for command in commands:
            self.assertNotIn("/bin/sh", command)
            self.assertIn("_launch.py", command)
        launcher_path = self.target / ".codex" / "hooks" / "_launch.py"
        self.assertIn(str(launcher_path), commands[0], "must point at the installed copy")

    @unittest.skipIf(os.name == "nt", "the rewrite is Windows-only")
    def test_off_windows_the_installed_file_matches_the_source(self) -> None:
        self.assertEqual(0, self.install().returncode)
        self.assertEqual(
            SHIPPED_HOOKS_JSON.read_bytes(),
            (self.target / ".codex" / "hooks.json").read_bytes(),
        )


class InstallRendered(unittest.TestCase):
    """The write path hooks.json takes on Windows, which bypasses install_file."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.target = Path(self._dir.name) / "nested" / "hooks.json"

    def test_matching_content_is_a_no_op(self) -> None:
        installer.install_rendered(self.target, "same", force=False)
        installer.install_rendered(self.target, "same", force=False)
        self.assertEqual("same", self.target.read_text(encoding="utf-8"))

    def test_differing_content_needs_force(self) -> None:
        installer.install_rendered(self.target, "first", force=False)
        with self.assertRaises(FileExistsError):
            installer.install_rendered(self.target, "second", force=False)
        self.assertEqual(
            "first", self.target.read_text(encoding="utf-8"), "content was clobbered"
        )
        installer.install_rendered(self.target, "second", force=True)
        self.assertEqual("second", self.target.read_text(encoding="utf-8"))


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

    def unique_hook_name(self) -> str:
        """A hook name no real install on this machine can already have."""
        return f"probe_{uuid.uuid4().hex}"

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
        # A name no real install can have. find_hook walks up from its start
        # directory, and on Windows tempfile puts that under %USERPROFILE% --
        # so a developer who has actually run `install --home` has
        # ~/.codex/hooks/stop.py sitting on the walk-up path, shadowing the
        # fixture and passing this for the wrong reason.
        name = self.unique_hook_name()
        self.set_home(self.tmp / "home")
        home_hook = self.make_hook(self.tmp / "home", name)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.assertEqual(home_hook, launcher.find_hook(name, elsewhere))

    def test_a_missing_hook_is_not_an_error(self) -> None:
        # A workspace with no Ix hooks installed is a normal state; it must not
        # fail the Codex turn.
        #
        # Unique name for the reason above, and here it is not just a false pass:
        # an ancestor `session_start.py` would be *executed* in-process, and it
        # reads stdin, so on a developer machine with a home install this hangs.
        name = self.unique_hook_name()
        self.set_home(self.tmp / "nowhere")
        os.chdir(self.tmp)
        self.assertIsNone(
            launcher.find_hook(name, self.tmp), "fixture leaked into a real install"
        )
        self.assertEqual(0, launcher.main(["_launch.py", name]))

    def test_the_hook_imports_its_own_common(self) -> None:
        """The other half of preferring the nearest hook.

        Every hook opens `from common import ...`. `python3 <hook>` puts the
        hook's directory on sys.path[0]; runpy.run_path does not, so without
        help the launcher's own directory answers instead — and a project-pinned
        hook silently imports the *home* copy of common.py, which is the exact
        opposite of the precedence find_hook just established. Silently wrong
        rather than an error, whenever the two copies differ.
        """
        name = self.unique_hook_name()
        project = self.tmp / "project"
        home = self.tmp / "home"
        self.set_home(home)

        for root, mark in ((project, "PROJECT"), (home, "HOME")):
            hooks = root / ".codex" / "hooks"
            hooks.mkdir(parents=True, exist_ok=True)
            (hooks / "common.py").write_text(f"MARK = {mark!r}" + chr(10), encoding="utf-8")
        self.make_hook(
            project, name, "from common import MARK" + chr(10) + "print(MARK)" + chr(10)
        )

        os.chdir(project)
        for stale in ("common", "ix_hook_common"):
            sys.modules.pop(stale, None)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(0, launcher.main(["_launch.py", name]))
        self.assertEqual("PROJECT", buffer.getvalue().strip())

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
