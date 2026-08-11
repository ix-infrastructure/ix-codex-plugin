#!/usr/bin/env python3
"""Every MCP tool must actually reach the `ix` CLI, and ask for JSON before parsing it.

Registering 23 tools says nothing about whether they run. Every tool but
`ix_health` used to pass only the subcommand to `subprocess.run`, so Python
tried to exec programs named `stats`, `locate` and `map`; each died in the
OSError branch and returned an error blob. `ix_health` was the one tool that
spelled the executable correctly, which is exactly what let the server look
alive while nothing in it worked. A tool count cannot see any of that.

So this drives all 23 through a fake `ix` on PATH that echoes the argv it was
handed, and asserts the full argv list. Two properties fall out of doing it
this way rather than by mocking `subprocess.run`:

  * `--format json` is *requested* rather than assumed. `_parse` looks for the
    first `{` or `[` in the output, so against human-oriented text it would
    either fail or, worse, succeed on a fragment of a rendered table.
  * arguments reach the CLI as argv rather than as text a shell re-parses. That
    property is platform-specific and is checked separately, in
    test_shell_metacharacters_never_reach_a_shell — on Windows there is no
    `ix.exe`, so the resolved `.CMD` runs via cmd.exe and the server refuses
    metacharacters rather than passing them.

Originally contributed by @Hiro-Chiba in #16; folded in here because #13
changes the same call path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]

# The same module object server.py imports, so resetting its memoised version
# here actually affects the server under test.
_llm_spec = importlib.util.spec_from_file_location(
    "ix_llm", REPO_ROOT / "mcp" / "ix_llm.py"
)
ix_llm = importlib.util.module_from_spec(_llm_spec)
sys.modules["ix_llm"] = ix_llm
_llm_spec.loader.exec_module(ix_llm)


class _FakeServer:
    """Stands in for FastMCP / MCPServer — only the constructor and .tool() matter."""

    def __init__(self, name: str) -> None:
        self.name = name

    def tool(self):
        return lambda function: function


def _load_server(sdk: str):
    """Import mcp/server.py against a faked MCP SDK.

    `sdk` selects which import line the server should find, so both branches of
    the version shim get exercised: 2.0.0 renamed FastMCP to MCPServer and moved
    it to mcp.server.mcpserver, and both spellings are in the wild because
    nothing in this repo pins the SDK.
    """
    mcp_module = types.ModuleType("mcp")
    mcp_server_module = types.ModuleType("mcp.server")
    modules = {"mcp": mcp_module, "mcp.server": mcp_server_module}

    if sdk == "v2":
        mcpserver_module = types.ModuleType("mcp.server.mcpserver")
        mcpserver_module.MCPServer = _FakeServer
        modules["mcp.server.mcpserver"] = mcpserver_module
    elif sdk == "v1":
        fastmcp_module = types.ModuleType("mcp.server.fastmcp")
        fastmcp_module.FastMCP = _FakeServer
        modules["mcp.server.fastmcp"] = fastmcp_module
        # Absent mcp.server.mcpserver must fall through to the v1 import.
        modules["mcp.server.mcpserver"] = None
    else:  # pragma: no cover - programming error
        raise ValueError(sdk)

    spec = importlib.util.spec_from_file_location(
        f"ix_mcp_server_{sdk}", REPO_ROOT / "mcp" / "server.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


# (attribute, args, kwargs, expected argv). `ix_health` probes --version and is
# here because being the only correct tool is what hid the bug.

CASES: list[tuple[str, tuple, dict, list]] = [
    ("ix_health",      (),                  {},                                           ["--version"]),
    ("ix_briefing",    (),                  {},                                           ["briefing", "--format", "json"]),
    # A symbol with a space, which is enough to prove the argument survives as one
    # argv element. Shell metacharacters are a separate matter and get their own
    # test — folding them in here made the sweep depend on a payload that has to
    # be space-free, which is not what these 23 cases are checking.
    ("ix_locate",      ("User Service",),   {},                                           ["locate", "User Service", "--format", "json"]),
    ("ix_text",        ("needle",),         {"limit": 7, "path": "src", "language": "python"},
     ["text", "needle", "--limit", "7", "--path", "src", "--language", "python", "--format", "json"]),
    ("ix_impact",      ("Widget",),         {},                                           ["impact", "Widget", "--format", "json"]),
    # Was pinned as bare ["map"] — locking in the one tool still parsing rendered
    # text as JSON, which is the defect the rest of this file exists to catch.
    ("ix_map",         (),                  {},                                           ["map", "--format", "json"]),
    ("ix_overview",    ("Widget",),         {},                                           ["overview", "Widget", "--format", "json"]),
    ("ix_read",        ("Widget",),         {},                                           ["read", "Widget", "--format", "json"]),
    ("ix_diff",        (3, 8),              {"target": "Widget", "summary": True},
     ["diff", "3", "8", "Widget", "--summary", "--format", "json"]),
    ("ix_callers",     ("Widget",),         {},                                           ["callers", "Widget", "--format", "json"]),
    ("ix_callees",     ("Widget",),         {},                                           ["callees", "Widget", "--format", "json"]),
    ("ix_imported_by", ("Widget",),         {},                                           ["imported-by", "Widget", "--format", "json"]),
    ("ix_imports",     ("Widget",),         {},                                           ["imports", "Widget", "--format", "json"]),
    ("ix_depends",     ("Widget",),         {"depth": 2},                                 ["depends", "Widget", "--depth", "2", "--format", "json"]),
    ("ix_trace",       ("Widget",),         {"to": "render"},                             ["trace", "Widget", "--to", "render", "--format", "json"]),
    ("ix_explain",     ("Widget",),         {},                                           ["explain", "Widget", "--format", "json"]),
    ("ix_rank",        (),                  {"by": "callers", "kind": "function", "top": 5, "path": "src"},
     ["rank", "--by", "callers", "--kind", "function", "--top", "5", "--path", "src", "--format", "json"]),
    ("ix_inventory",   ("src",),            {"kind": "function"},
     ["inventory", "--kind", "function", "--path", "src", "--format", "json"]),
    # limit is applied client-side to the parsed candidates, so it is deliberately
    # not forwarded to the CLI.
    ("ix_smells",      (),                  {"path": "src", "limit": 10},                 ["smells", "--path", "src", "--format", "json"]),
    ("ix_stats",       (),                  {},                                           ["stats", "--format", "json"]),
    ("ix_subsystems",  (),                  {},                                           ["subsystems", "--format", "json"]),
    ("ix_decisions",   (),                  {"path": "src"},                              ["decisions", "--path", "src", "--format", "json"]),
    ("ix_history",     ("Widget",),         {},                                           ["history", "Widget", "--format", "json"]),
]

FAKE_IX = """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_IX_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")

if sys.argv[1:] == ["--version"]:
    print("0.9.1")
else:
    print(json.dumps({"argv": sys.argv[1:]}))
"""


FAILING_IX = """#!/usr/bin/env python3
import sys

sys.stderr.write("graph not ingested; run ix init\\n")
sys.exit(2)
"""


def _write_fake_ix(directory: Path, source: str = FAKE_IX) -> None:
    """Put a fake `ix` on PATH that this platform can actually execute.

    A bare `ix` carrying a `#!` line works only on POSIX. Windows cannot exec a
    shebang script at all, so the stub never ran, nothing was ever logged, and
    both argv tests died reading a file that was never created — on the one PR
    whose subject is making this work on Windows. `.bat` is in the default
    PATHEXT, so `shutil.which("ix")` (what the server uses to resolve it) finds
    this and hands off to the interpreter running the tests.
    """
    if os.name == "nt":
        impl = directory / "ix_impl.py"
        impl.write_text(source, encoding="utf-8")
        (directory / "ix.bat").write_text(
            f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n', encoding="utf-8"
        )
        return
    fake_ix = directory / "ix"
    fake_ix.write_text(source, encoding="utf-8")
    fake_ix.chmod(0o755)


class McpCliInvocationTest(unittest.TestCase):
    def setUp(self) -> None:
        """Pin the argv contract this file exists for: JSON, deterministically.

        The llm fast-path rewrites the format token, and whether it engages
        depends on a version probe memoised in ix_llm for the life of the
        process. That made these 23 assertions depend on what ran before them:
        alone the cache was already primed and they passed, under `unittest
        discover` test_llm_fastpath had just reset it, so the probe ran against
        this file's own fake `ix` (which reports 0.9.1), the fast-path engaged
        and every expected `--format json` became `llm`.

        Disabled here rather than accommodated: this file pins that every tool
        reaches the CLI and asks for a machine format. Which token a current CLI
        gets asked for is test_llm_fastpath's job.
        """
        ix_llm.reset_version_cache()
        self.addCleanup(ix_llm.reset_version_cache)
        patcher = patch.dict(os.environ, {"IX_DISABLE_LLM_FORMAT": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drive_all_tools(self, sdk: str):
        server = _load_server(sdk)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "argv.jsonl"
            marker_path = temp_path / "shell-was-invoked"
            _write_fake_ix(temp_path)

            def resolve(value):
                return value

            environment = {
                "FAKE_IX_LOG": str(log_path),
                "PATH": f"{temp_path}{os.pathsep}{os.environ.get('PATH', '')}",
            }

            results = []
            with patch.dict(os.environ, environment):
                for name, args, kwargs, _expected in CASES:
                    tool = getattr(server, name)
                    results.append(tool(*(resolve(a) for a in args), **kwargs))

            expected = [[resolve(part) for part in argv] for _n, _a, _k, argv in CASES]
            actual = [json.loads(line) for line in log_path.read_text().splitlines()]
            return results, expected, actual, marker_path

    def test_v1_sdk_all_tools_invoke_ix_with_expected_arguments(self) -> None:
        results, expected, actual, marker = self._drive_all_tools("v1")
        self.assertEqual(expected, actual)
        self.assertEqual(len(CASES), len(results))
        self.assertEqual(23, len(CASES), "every registered tool should be covered here")
        self.assertFalse(marker.exists(), "arguments must reach ix as argv, never via a shell")
        self.assertTrue(all("error" not in json.loads(r) for r in results))

    def test_v2_sdk_all_tools_invoke_ix_with_expected_arguments(self) -> None:
        results, expected, actual, marker = self._drive_all_tools("v2")
        self.assertEqual(expected, actual)
        self.assertFalse(marker.exists())
        self.assertTrue(all("error" not in json.loads(r) for r in results))

    def test_shell_metacharacters_never_reach_a_shell(self) -> None:
        """A space-free payload — the only kind that can reach one.

        `subprocess` quotes an argument only when it contains whitespace, so
        `Widget & touch x` is protected by that quoting and passes this check
        whether or not a shell is in the chain. It has to be space-free to test
        anything, which is why the control below runs first: if the payload
        cannot fire even through an explicit shell, the assertion that follows
        means nothing.

        The two platforms are legitimately different. On POSIX the fake `ix` is
        exec'd directly and the payload arrives verbatim as one argv element. On
        Windows there is no `ix.exe` to exec — `shutil.which` finds a `.CMD`, and
        CreateProcess runs those through `cmd.exe` — so the server refuses the
        argument instead, and it must not reach the CLI at all.
        """
        server = _load_server("v1")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "argv.jsonl"
            marker_path = temp_path / "shell-was-invoked"
            self.assertNotIn(" ", str(marker_path), "payload must stay space-free")
            _write_fake_ix(temp_path)
            payload = f"Widget&echo>{marker_path}"
            environment = {
                "FAKE_IX_LOG": str(log_path),
                "PATH": f"{temp_path}{os.pathsep}{os.environ.get('PATH', '')}",
            }

            # Positive control, in the shape the bug actually has: the payload
            # handed to subprocess as an argv element, exactly as _run does it,
            # with nothing guarding it. Running it through `shell=True` instead
            # would prove only that a shell is a shell -- it is insensitive to
            # the quoting rule that makes this payload dangerous and that one
            # safe, so re-adding spaces would leave the control green.
            with patch.dict(os.environ, environment):
                resolved = shutil.which("ix", path=str(temp_path))
                self.assertIsNotNone(resolved)
                subprocess.run(
                    [resolved, "locate", payload], capture_output=True
                )
            if os.name == "nt":
                self.assertTrue(
                    marker_path.exists(),
                    "control failed: an unguarded argv call did not fire the "
                    "payload, so the assertion below would pass for the wrong "
                    "reason -- check the payload is still space-free",
                )
                marker_path.unlink()
            else:
                # No shim, no shell: nothing to escape, and the control is that
                # the CLI receives it whole.
                self.assertFalse(marker_path.exists())
            log_path.unlink(missing_ok=True)

            with patch.dict(os.environ, environment):
                result = json.loads(server.ix_locate(payload))

            self.assertFalse(marker_path.exists(), "the payload reached a shell")
            logged = (
                [json.loads(x) for x in log_path.read_text().splitlines()]
                if log_path.exists()
                else []
            )
            if os.name == "nt":
                self.assertIn("error", result)
                self.assertIn("refusing to run", result["error"])
                self.assertEqual([], logged, "the argument must not reach the CLI")
            else:
                self.assertEqual([["locate", payload, "--format", "json"]], logged)

    @unittest.skipUnless(os.name == "nt", "cmd.exe shim only exists on Windows")
    def test_every_refused_character_is_refused(self) -> None:
        """One payload carrying several metacharacters cannot pin the set.

        With `Widget&echo>MARKER` as the only case, dropping either `&` or `>`
        from the refused set leaves the test green, and `%` is never exercised
        at all — so a later "fix" for the `Vec<T>` false positive could quietly
        reopen the hole.
        """
        server = _load_server("v1")
        for char in "&|<>^\"%!":
            with self.subTest(char=char), tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                _write_fake_ix(temp_path)
                environment = {
                    "FAKE_IX_LOG": str(temp_path / "argv.jsonl"),
                    "PATH": f"{temp_path}{os.pathsep}{os.environ.get('PATH', '')}",
                }
                # Run from a scratch directory so the `>` case redirects there
                # rather than into the repo if the guard ever breaks -- that is
                # how a file called `x` once got committed. It must NOT be the
                # directory holding the fake ix: shutil.which searches the
                # working directory first on Windows, the resolved path would
                # then be relative, and the isabs refusal would answer before
                # the metacharacter check ever ran -- leaving this green for a
                # reason that has nothing to do with the character under test.
                with tempfile.TemporaryDirectory() as elsewhere:
                    cwd = os.getcwd()
                    os.chdir(elsewhere)
                    try:
                        with patch.dict(os.environ, environment):
                            result = json.loads(server.ix_locate(f"Widget{char}x"))
                    finally:
                        os.chdir(cwd)
                self.assertIn("refusing to run", result.get("error", ""))
                self.assertIn(repr(char), result["error"])

    @unittest.skipUnless(os.name == "nt", "cmd.exe shim only exists on Windows")
    def test_a_newline_in_an_argument_is_refused(self) -> None:
        """CR and LF end a command line as surely as `&` splits one."""
        server = _load_server("v1")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_fake_ix(temp_path)
            environment = {
                "FAKE_IX_LOG": str(temp_path / "argv.jsonl"),
                "PATH": f"{temp_path}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            for char in ("\r", "\n"):
                with self.subTest(char=char), patch.dict(os.environ, environment):
                    result = json.loads(server.ix_locate(f"Widget{char}whoami"))
                self.assertIn("refusing to run", result.get("error", ""))

    @unittest.skipUnless(os.name == "nt", "cmd.exe shim only exists on Windows")
    def test_a_metacharacter_in_the_resolved_path_is_refused(self) -> None:
        """list2cmdline leaves an unquoted path alone, so `C:\\a&b\\ix.cmd` splits."""
        server = _load_server("v1")
        with tempfile.TemporaryDirectory() as temp_dir:
            bad_dir = Path(temp_dir) / "a&b"
            bad_dir.mkdir()
            _write_fake_ix(bad_dir)
            environment = {
                "FAKE_IX_LOG": str(bad_dir / "argv.jsonl"),
                "PATH": f"{bad_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            with patch.dict(os.environ, environment):
                result = json.loads(server.ix_locate("Widget"))
            self.assertIn("refusing to run", result.get("error", ""))

    @unittest.skipUnless(
        os.name == "nt", "CPython only inserts os.curdir into the search on Windows"
    )
    def test_a_working_directory_ix_is_not_executed(self) -> None:
        """shutil.which prefers the CWD on Windows, `path=` notwithstanding."""
        server = _load_server("v1")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_fake_ix(temp_path)
            # PATH must be a real directory that simply has no `ix`: an empty
            # PATH makes shutil.which give up before it ever consults the
            # working directory, which would make this pass for the wrong
            # reason. With a populated PATH it still returns `.\ix.bat`.
            elsewhere = tempfile.mkdtemp()
            self.addCleanup(os.rmdir, elsewhere)
            cwd = os.getcwd()
            os.chdir(temp_path)
            try:
                with patch.dict(
                    os.environ,
                    {"PATH": elsewhere, "FAKE_IX_LOG": str(temp_path / "l")},
                    clear=False,
                ):
                    os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
                    self.assertIsNotNone(
                        shutil.which("ix", path=elsewhere),
                        "control: shutil.which should still find the CWD copy, "
                        "otherwise this test proves nothing",
                    )
                    result = json.loads(server.ix_locate("Widget"))
            finally:
                # Before the TemporaryDirectory is torn down: Windows cannot
                # remove a directory that is some process's working directory.
                os.chdir(cwd)
            self.assertIn("error", result)
            self.assertFalse(
                (temp_path / "l").exists(), "the working-directory ix was executed"
            )

    def test_the_reason_a_call_failed_reaches_the_caller(self) -> None:
        """_json hands its callers bare data, so stderr had nowhere to go.

        Both routes: the early returns in _run, and a CLI that actually ran and
        exited non-zero. The second is the one a user hits normally.
        """
        server = _load_server("v1")
        with patch.dict(os.environ, {"PATH": ""}):
            result = json.loads(server.ix_locate("Widget"))
        self.assertIn("ix CLI not found", result.get("error", ""))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            _write_fake_ix(temp_path, source=FAILING_IX)
            environment = {
                "FAKE_IX_LOG": str(temp_path / "argv.jsonl"),
                "PATH": f"{temp_path}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            with patch.dict(os.environ, environment):
                result = json.loads(server.ix_locate("Widget"))
        self.assertIn("graph not ingested", result.get("error", ""))

    def test_every_registered_tool_is_covered(self) -> None:
        """A tool added without a case here would otherwise go untested silently."""
        server = _load_server("v1")
        exported = {
            name
            for name in dir(server)
            if name.startswith("ix_") and callable(getattr(server, name))
        }
        self.assertEqual(exported, {name for name, _a, _k, _e in CASES})


if __name__ == "__main__":
    unittest.main(verbosity=2)
