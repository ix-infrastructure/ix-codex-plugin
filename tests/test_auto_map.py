#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""When the hooks may run `ix map` on their own, and exactly what they run.

Two things were wrong at once. `find_workspace_root` stopped at the first
`.codex/hooks.json` above the session's cwd -- and a `--home` install puts one in
`~/.codex/`, so with the default install every hook resolved to `$HOME` and the
Stop hook ran `ix map` there on every turn. And the PostToolUse hook ran
`ix map <file>` after each write, which the CLI has rejected since v0.10.6
("Map path is not a directory").

An automatic map now has to clear every guard: a git repository, not `$HOME`,
already mapped (`graphCompleted: true`), outside its debounce window -- and then
it is exactly `ix map <root> --silent`, from the root, with `IX_AUTO_MAP=1`,
detached.

The fake CLI below is strict on purpose. A fake that answers anything would
have passed the per-file map and `ix locate --limit` too.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".codex" / "hooks"
HAS_GIT = shutil.which("git") is not None
REAL_RUN = subprocess.run
# subprocess.run is built on Popen, so patching Popen reaches the real `git`
# calls too; those are handed straight back to the real class.
REAL_POPEN = subprocess.Popen


def _is_git(argv) -> bool:
    return Path(argv[0]).stem.lower() == "git"


def _load(*names: str):
    """Fresh `common` plus the named hook modules, importing that `common`."""
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    sys.modules.pop("common", None)
    spec = importlib.util.spec_from_file_location("common", HOOKS_DIR / "common.py")
    common = importlib.util.module_from_spec(spec)
    sys.modules["common"] = common
    spec.loader.exec_module(common)
    hooks = []
    for name in names:
        sys.modules.pop(name, None)
        hook_spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(module)
        hooks.append(module)
    return (common, *hooks)


def _git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    REAL_RUN(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path.resolve()


def _same(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


class StrictFakeIx:
    """The Ix CLI as far as these hooks use it -- and no further.

    Rejects what the real CLI (v0.11.1 and the pending merges) rejects:
    `map` of anything that is not a directory, `locate --limit`,
    `smells --path`, and commands that do not exist. `git` passes through to the
    real binary, because the guard's answer depends on a real work tree.
    """

    UNKNOWN_OPTIONS = {"locate": {"--limit"}, "smells": {"--path"}}
    COMMANDS = {
        "status", "map", "text", "locate", "inventory", "overview",
        "impact", "briefing", "bug", "smells",
    }

    def __init__(self, graph_completed: object = True, status: str | None = None) -> None:
        self.graph_completed = graph_completed
        self.status_stdout = status
        self.runs: list[list[str]] = []
        self.spawned: list[dict] = []
        self.errors: list[str] = []

    def _reject(self, args: list[str]) -> str | None:
        command = args[0] if args else ""
        if command not in self.COMMANDS:
            return f"error: unknown command '{command}'"
        for option in self.UNKNOWN_OPTIONS.get(command, ()):
            if any(a == option or a.startswith(option + "=") for a in args[1:]):
                return f"error: unknown option '{option}'"
        if command == "map":
            positional = [a for a in args[1:] if not a.startswith("-")]
            if positional and not Path(positional[0]).is_dir():
                return f"Map path is not a directory: {positional[0]}"
        return None

    def run(self, argv, **kwargs):
        if _is_git(argv):
            return REAL_RUN(argv, **kwargs)
        args = list(argv[1:])
        self.runs.append(args)
        error = self._reject(args)
        if error:
            self.errors.append(error)
            return subprocess.CompletedProcess(argv, 1, "", error + "\n")
        if args[0] == "status":
            if self.status_stdout is not None:
                return subprocess.CompletedProcess(argv, 0, self.status_stdout, "")
            payload = {"backend": "ok", "graphCompleted": self.graph_completed}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    def popen(self, argv, *a, **kwargs):
        if _is_git(argv):
            return REAL_POPEN(argv, *a, **kwargs)
        args = list(argv[1:])
        error = self._reject(args)
        if error:
            self.errors.append(error)
        self.spawned.append({"args": args, **kwargs})
        return None


class GuardedAutoMapTest(unittest.TestCase):
    def setUp(self) -> None:
        if not HAS_GIT:
            self.skipTest("git is not installed")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.common, self.stop, self.post = _load("stop", "post_tool_use")
        self.common.CACHE_DIR = self.tmp / "state"
        # A home of our own, so nothing here depends on (or writes near) the
        # developer's real one.
        self.home = self.tmp / "home"
        self.home.mkdir()
        home_patch = patch.object(self.common, "_home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.repo = _git_init(self.home / "src" / "repo")

    def _request(self, fake: StrictFakeIx, cwd) -> bool:
        with patch.object(self.common.subprocess, "run", fake.run), patch.object(
            self.common.subprocess, "Popen", fake.popen
        ):
            return self.common.request_auto_map(cwd)

    def _assert_one_map_of(self, fake: StrictFakeIx, root: Path) -> None:
        self.assertEqual([], fake.errors)
        self.assertEqual(1, len(fake.spawned), fake.spawned)
        spawn = fake.spawned[0]
        self.assertEqual(["map", spawn["args"][1], "--silent"], spawn["args"])
        self.assertTrue(_same(spawn["args"][1], root), spawn["args"])
        self.assertTrue(_same(spawn["cwd"], root), spawn["cwd"])
        self.assertEqual("1", spawn["env"]["IX_AUTO_MAP"])
        if os.name == "nt":
            self.assertTrue(spawn.get("creationflags"))
        else:
            self.assertTrue(spawn.get("start_new_session"))

    # ── the guards ───────────────────────────────────────────────────────────

    def test_not_a_git_repository_is_never_mapped(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        fake = StrictFakeIx()
        self.assertFalse(self._request(fake, str(plain)))
        self.assertEqual([], fake.spawned)
        self.assertEqual([], fake.runs, "no `ix` call at all outside a repository")

    def test_home_is_never_mapped_even_as_a_git_repository(self) -> None:
        _git_init(self.home)  # a dotfiles repo
        plain = self.home / "notes"
        plain.mkdir()
        fake = StrictFakeIx()
        for cwd in (self.home, plain):
            with self.subTest(cwd=cwd):
                self.assertFalse(self._request(fake, str(cwd)))
        self.assertEqual([], fake.spawned)
        self.assertEqual([], fake.runs)

    def test_no_project_dir_is_never_mapped(self) -> None:
        fake = StrictFakeIx()
        for cwd in (None, ""):
            self.assertFalse(self._request(fake, cwd))
        self.assertEqual([], fake.spawned)

    def test_an_unmapped_project_is_never_mapped(self) -> None:
        fake = StrictFakeIx(graph_completed=False)
        self.assertFalse(self._request(fake, str(self.repo)))
        self.assertEqual([], fake.spawned)
        self.assertEqual(1, len(fake.runs))
        status = fake.runs[0]
        self.assertEqual(["status", "--format", "json", "--root"], status[:4])
        self.assertTrue(_same(status[4], self.repo))

    def test_anything_but_an_explicit_true_is_not_mapped(self) -> None:
        for name, fake in (
            ("truthy string", StrictFakeIx(graph_completed="true")),
            ("missing field", StrictFakeIx(status='{"backend": "ok"}')),
            ("not json", StrictFakeIx(status="Backend: ok\nGraph: complete\n")),
            ("empty", StrictFakeIx(status="")),
        ):
            with self.subTest(name):
                self.common.CACHE_DIR = self.tmp / f"state-{name.replace(' ', '-')}"
                self.assertFalse(self._request(fake, str(self.repo)))
                self.assertEqual([], fake.spawned)

    def test_a_failing_status_is_not_mapped(self) -> None:
        fake = StrictFakeIx()

        def failing(argv, **kwargs):
            if _is_git(argv):
                return REAL_RUN(argv, **kwargs)
            raise subprocess.TimeoutExpired(argv, 4)

        with patch.object(self.common.subprocess, "run", failing), patch.object(
            self.common.subprocess, "Popen", fake.popen
        ):
            self.assertFalse(self.common.request_auto_map(str(self.repo)))
        self.assertEqual([], fake.spawned)

    def test_a_mapped_project_gets_exactly_one_silent_root_map(self) -> None:
        fake = StrictFakeIx()
        self.assertTrue(self._request(fake, str(self.repo)))
        self._assert_one_map_of(fake, self.repo)

    def test_a_subdirectory_maps_the_repository_root(self) -> None:
        sub = self.repo / "src" / "pkg"
        sub.mkdir(parents=True)
        fake = StrictFakeIx()
        self.assertTrue(self._request(fake, str(sub)))
        self._assert_one_map_of(fake, self.repo)

    def test_a_second_request_inside_the_window_does_nothing(self) -> None:
        fake = StrictFakeIx()
        self.assertTrue(self._request(fake, str(self.repo)))
        self.assertFalse(self._request(fake, str(self.repo)))
        self.assertEqual(1, len(fake.spawned))
        self.assertEqual(1, len(fake.runs), "the debounce must also skip `ix status`")

    def test_the_window_expires(self) -> None:
        fake = StrictFakeIx()
        self.assertTrue(self._request(fake, str(self.repo)))
        stamp = self.common._auto_map_stamp_path(self.repo)
        stamp.write_text(str(time.time() - self.common.AUTO_MAP_DEBOUNCE_SECONDS - 1))
        self.assertTrue(self._request(fake, str(self.repo)))
        self.assertEqual(2, len(fake.spawned))

    def test_two_repositories_do_not_debounce_each_other(self) -> None:
        other = _git_init(self.home / "src" / "other")
        fake = StrictFakeIx()
        self.assertTrue(self._request(fake, str(self.repo)))
        self.assertTrue(self._request(fake, str(other)))
        self.assertEqual(2, len(fake.spawned))
        self.assertTrue(_same(fake.spawned[0]["cwd"], self.repo))
        self.assertTrue(_same(fake.spawned[1]["cwd"], other))

    def test_no_map_when_the_debounce_cannot_be_recorded(self) -> None:
        """Without a stamp every turn would map; refusing is the safe side."""
        blocker = self.tmp / "not-a-dir"
        blocker.write_text("")
        self.common.CACHE_DIR = blocker / "state"
        fake = StrictFakeIx()
        self.assertFalse(self._request(fake, str(self.repo)))
        self.assertEqual([], fake.spawned)

    # ── the hooks that ask for it ────────────────────────────────────────────

    def _run_hook(self, hook, event: dict, fake: StrictFakeIx) -> list:
        emitted: list = []
        with patch.object(self.common.subprocess, "run", fake.run), patch.object(
            self.common.subprocess, "Popen", fake.popen
        ), patch.object(hook, "read_event", return_value=event), patch.object(
            hook, "ix_available", return_value=True
        ), patch.object(
            self.common, "emit_json", side_effect=emitted.append
        ):
            if hasattr(hook, "emit_json"):
                with patch.object(hook, "emit_json", side_effect=emitted.append):
                    hook.main()
            else:
                hook.main()
        return emitted

    def test_stop_maps_the_payload_cwd_repository(self) -> None:
        fake = StrictFakeIx()
        emitted = self._run_hook(self.stop, {"cwd": str(self.repo)}, fake)
        self._assert_one_map_of(fake, self.repo)
        self.assertEqual([{"continue": True}], emitted)

    def test_stop_in_home_runs_nothing(self) -> None:
        # Codex's own ~/.codex/hooks.json is what used to pull every session here.
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "hooks.json").write_text("{}")
        fake = StrictFakeIx()
        emitted = self._run_hook(self.stop, {"cwd": str(self.home)}, fake)
        self.assertEqual([], fake.runs)
        self.assertEqual([], fake.spawned)
        self.assertEqual([{"continue": True}], emitted)

    def test_a_write_requests_the_root_map_never_a_file_map(self) -> None:
        (self.repo / "app.py").write_text("x = 1\n")
        fake = StrictFakeIx()
        event = {"cwd": str(self.repo), "tool_input": {"command": "echo y > app.py"}}
        self._run_hook(self.post, event, fake)
        self._assert_one_map_of(fake, self.repo)

    def test_a_command_that_writes_nothing_requests_nothing(self) -> None:
        fake = StrictFakeIx()
        event = {"cwd": str(self.repo), "tool_input": {"command": "ls -la"}}
        self._run_hook(self.post, event, fake)
        self.assertEqual([], fake.runs)
        self.assertEqual([], fake.spawned)

    def test_write_and_stop_share_one_window(self) -> None:
        fake = StrictFakeIx()
        event = {"cwd": str(self.repo), "tool_input": {"command": "echo y > app.py"}}
        self._run_hook(self.post, event, fake)
        self._run_hook(self.stop, {"cwd": str(self.repo)}, fake)
        self.assertEqual(1, len(fake.spawned))


class WorkspaceRootTest(unittest.TestCase):
    """Which directory the query hooks run `ix` from."""

    def setUp(self) -> None:
        if not HAS_GIT:
            self.skipTest("git is not installed")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        (self.common,) = _load()
        self.home = self.tmp / "home"
        (self.home / ".codex").mkdir(parents=True)
        (self.home / ".codex" / "hooks.json").write_text("{}")
        home_patch = patch.object(self.common, "_home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)

    def test_the_git_root_wins_over_a_home_install(self) -> None:
        repo = _git_init(self.home / "src" / "repo")
        sub = repo / "pkg"
        sub.mkdir()
        self.assertTrue(_same(self.common.find_workspace_root(str(sub)), repo))

    def test_home_is_never_a_workspace(self) -> None:
        self.assertIsNone(self.common.find_workspace_root(str(self.home)))
        _git_init(self.home)
        self.assertIsNone(self.common.find_workspace_root(str(self.home)))

    def test_outside_git_the_directory_itself(self) -> None:
        plain = self.home / "notes"
        plain.mkdir()
        self.assertTrue(_same(self.common.find_workspace_root(str(plain)), plain))


class StrictFakeQueriesTest(unittest.TestCase):
    """The query hooks' argv survives a CLI that rejects what the real one does."""

    def test_search_interception_passes_the_strict_cli(self) -> None:
        (common,) = _load()
        fake = StrictFakeIx()
        with patch.object(common.subprocess, "run", fake.run):
            common.build_search_message("IxClient", None)
        self.assertEqual([], fake.errors)
        locate = [r for r in fake.runs if r[0] == "locate"]
        self.assertEqual([["locate", "IxClient", "--format", "json"]], locate)

    def test_the_fake_is_actually_strict(self) -> None:
        # Or every test above passes vacuously.
        fake = StrictFakeIx()
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as handle:
            file_path = handle.name
        self.addCleanup(os.unlink, file_path)
        for argv in (
            ["ix", "map", file_path],
            ["ix", "locate", "X", "--limit", "5"],
            ["ix", "smells", "--path", "src"],
            ["ix", "bugs"],
        ):
            with self.subTest(argv):
                self.assertEqual(1, fake.run(argv).returncode)


class StateDirTest(unittest.TestCase):
    def test_not_a_shared_temp_path(self) -> None:
        (common,) = _load()
        shared = Path(tempfile.gettempdir()) / "ix-codex-hooks"
        self.assertNotEqual(shared, common.CACHE_DIR)
        self.assertEqual("ix-codex-plugin", common.CACHE_DIR.name)

    @unittest.skipIf(os.name == "nt", "XDG applies to POSIX only")
    def test_xdg_state_home_is_honoured_when_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"XDG_STATE_HOME": tmp}
        ):
            (common,) = _load()
            self.assertEqual(Path(tmp) / "ix-codex-plugin", common.CACHE_DIR)
        with patch.dict(os.environ, {"XDG_STATE_HOME": "relative/dir"}):
            (common,) = _load()
            self.assertTrue(common.CACHE_DIR.is_absolute())

    def test_an_unwritable_cache_never_raises(self) -> None:
        (common,) = _load()
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("")
            common.CACHE_DIR = blocker / "state"
            common.STATUS_CACHE_PATH = common.CACHE_DIR / "ix-status.json"
            common.BRIEFING_CACHE_PATH = common.CACHE_DIR / "ix-briefing.txt"
            self.assertFalse(common._write_cache(common.STATUS_CACHE_PATH, {"ok": True}))
            common.mark_briefing_sent(tmp)  # must not raise
            self.assertTrue(common.briefing_due(tmp))

    @unittest.skipUnless(hasattr(os, "getuid"), "POSIX ownership check")
    def test_a_directory_owned_by_someone_else_is_refused(self) -> None:
        (common,) = _load()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(common.os, "getuid", return_value=os.getuid() + 1):
                self.assertFalse(common._private_dir(Path(tmp)))


@unittest.skipUnless(os.name == "posix" and HAS_GIT, "spawns a shell-script fake ix")
class StopHookEndToEndTest(unittest.TestCase):
    """The real hook process, a fake `ix` on PATH, and nothing mocked."""

    FAKE_IX = """#!/bin/sh
log="$IX_FAKE_LOG"
if [ "$1" = status ]; then
  printf '{"backend":"ok","graphCompleted":true}\\n'
  exit 0
fi
if [ "$1" = map ]; then
  if [ -n "$2" ] && [ "${2#-}" = "$2" ] && [ ! -d "$2" ]; then
    echo "Map path is not a directory: $2" >&2
    echo "ERROR map-not-dir" >> "$log"
    exit 1
  fi
  printf 'map|%s|%s|%s|%s\\n' "$*" "$PWD" "${IX_AUTO_MAP:-}" "$(id -u)" >> "$log"
  exit 0
fi
echo "unexpected: $*" >> "$log"
exit 1
"""

    def test_stop_spawns_one_detached_guarded_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp).resolve()
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            fake = bin_dir / "ix"
            fake.write_text(self.FAKE_IX)
            fake.chmod(0o755)
            log = tmp_path / "ix.log"
            home = tmp_path / "home"
            repo = _git_init(home / "repo")
            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "HOME": str(home),
                "XDG_STATE_HOME": str(tmp_path / "state"),
                "IX_FAKE_LOG": str(log),
            }
            env.pop("IX_AUTO_MAP", None)
            event = json.dumps({"cwd": str(repo)})
            for _ in range(2):  # the second one must be debounced
                result = REAL_RUN(
                    [sys.executable, str(HOOKS_DIR / "stop.py")],
                    input=event, capture_output=True, text=True, env=env,
                    cwd=str(home), timeout=30,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual({"continue": True}, json.loads(result.stdout))

            deadline = time.time() + 10
            while time.time() < deadline and not log.exists():
                time.sleep(0.05)
            time.sleep(0.3)  # let a (wrong) second map land if there were one
            lines = log.read_text().splitlines()
            self.assertEqual(1, len(lines), lines)
            _, args, cwd, auto, _uid = lines[0].split("|")
            self.assertEqual(f"map {repo} --silent", args)
            self.assertEqual(str(repo), cwd)
            self.assertEqual("1", auto)


if __name__ == "__main__":
    unittest.main(verbosity=2)
