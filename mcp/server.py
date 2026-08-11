#!/usr/bin/env python3
"""ix-memory MCP server — stdio transport, MCP Python SDK.

23 tools mirroring the Cursor plugin tool set.  All tools call the ix CLI
directly (same fallback strategy as the other hooks).  Future work will
migrate each to call_runtime() once the v2 runtime API is live.

Registration (run once after install):
  codex mcp add ix-memory -- python3 /path/to/.codex/mcp/server.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from typing import Optional

try:
    from ix_llm import try_llm
except ImportError:  # pragma: no cover - degraded but functional
    # `ix_llm.py` is installed beside this file by install_mcp(). If it is
    # absent — a hand-copied server.py, a partial install, an older layout —
    # every read tool falls back to the JSON path it used before the fast-path
    # existed. An ImportError here would instead take down all 23 tools to save
    # tokens on some of them, which is not a trade worth making.
    def try_llm(*_args: object, **_kwargs: object) -> Optional[str]:
        return None

# The MCP Python SDK renamed FastMCP to MCPServer in 2.0.0 and moved it from
# mcp.server.fastmcp to mcp.server.mcpserver. Nothing in this repo installs or
# pins the SDK — the installer copies this file and prints a `codex mcp add`
# line — so whichever version pip last resolved is what this has to run on, and
# both lines are in the wild. On a fresh `pip install mcp` the v1 import raises
# ModuleNotFoundError, and all Codex surfaces is that the ix-memory client
# "failed to start".
#
# Only the constructor moved. Everything below is unchanged across the two:
# @mcp.tool() keeps its name and signature, the tools stay plain functions
# returning str, and run(transport="stdio") is the same call. None of them use
# the get_context() that v2 removed, so there is nothing to adapt per tool.
try:  # mcp >= 2.0.0
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp < 2.0.0
    try:
        from mcp.server.fastmcp import FastMCP as _Server
    except ImportError as exc:  # the SDK is missing outright, not merely older
        raise SystemExit(
            "ix-memory MCP server requires the MCP Python SDK, which is not "
            "installed for this interpreter.\n"
            "  pip install mcp"
        ) from exc

mcp = _Server("ix-memory")


# ── Helpers ───────────────────────────────────────────────────────────────────

# Resolving the executable is necessary on Windows (see _run) but it is not free:
# npm ships no `ix.exe`, so shutil.which returns `ix.CMD`, and CreateProcess runs
# a .cmd/.bat by handing the command line to `cmd.exe /c`. Every argument is then
# parsed by a shell before the CLI sees it -- and subprocess only quotes
# arguments containing whitespace, so `Widget&whoami` splits into two commands
# while `Widget & whoami` does not. `%VAR%` is expanded either way, and a literal
# `"` escapes the quoting that protects the rest.
#
# These arguments come from tool calls the model makes, so the content is not
# trusted. Quoting correctly for cmd.exe is a well-known trap -- it is what
# CVE-2024-24576 was -- so this refuses the input instead of trying to escape it.
# The cost is that a symbol named with one of these cannot be queried on Windows,
# which beats running it.
_CMD_SHIM_SUFFIXES = (".cmd", ".bat")
# `!` is here for shims that enable delayed expansion: it cannot split a command
# (expansion happens after tokenisation) but it does substitute an environment
# value into the query, which is the same disclosure `%VAR%` gives.
_CMD_METACHARACTERS = frozenset('&|<>^"%!\r\n')


def _unsafe_for_cmd_shim(executable: str, args: list[str]) -> str:
    """The cmd.exe metacharacters in `args`, if this executable routes through one."""
    if not executable.lower().endswith(_CMD_SHIM_SUFFIXES):
        return ""
    found: set[str] = set()
    # The executable too: list2cmdline leaves an unquoted path alone, so an `ix`
    # installed under `C:\Users\a&b\` splits at the `&`. Not attacker-controlled,
    # but it fails silently and is one line to catch.
    for arg in (executable, *args):
        found |= set(arg) & _CMD_METACHARACTERS
    return " ".join(repr(c) for c in sorted(found))


# Why the reason a call failed is stashed rather than returned: _json hands its
# 21 callers bare data, so stderr had nowhere to go and every failure read
# "failed without output" -- including "ix CLI not found" and the refusal above,
# the two a user most needs to see. Threading a second value through 21 call
# sites is the tidier fix and a much larger diff; this is the small one.
#
# It is thread-local and written by *every* _run, including the early returns.
# A module-level string would be wrong twice over: mcp >= 2.0.0 dispatches each
# message with anyio.to_thread.run_sync, so two tools really can be in flight at
# once; and even single-threaded, ix_map and ix_health call _run directly, so a
# stale value from an earlier tool's _json would be reported under theirs --
# which for ix_read means one tool's file content surfacing in another's error.
_call_state = threading.local()


def _record_stderr(stderr: str) -> None:
    _call_state.stderr = stderr


def _recorded_stderr() -> str:
    return getattr(_call_state, "stderr", "")


def _run(args: list[str], timeout: int = 15) -> tuple[bool, str, str]:
    """Run `ix` with the given subcommand and flags.

    The executable is prepended here rather than written out at each of the 23
    tools, because that is exactly what went wrong: every tool but ix_health
    passed only the subcommand, so Python tried to execute programs named
    `stats`, `locate` and `map`. One tool spelling it correctly is what let the
    server look alive while nothing else in it worked.

    It is resolved with shutil.which rather than passed as the bare string "ix".
    On Windows the installed CLI is `ix.CMD`, and CreateProcess does not consult
    PATHEXT the way the shell does -- `subprocess.run(["ix", ...])` raises
    FileNotFoundError there however well-formed the rest of the argv is. Getting
    the argv right and still naming the executable in a way Windows cannot
    resolve would have left all 23 tools returning an error on the platform this
    is meant to fix.
    """
    # shutil.which searches the *current directory first* on Windows unless
    # NoDefaultCurrentDirectoryInExePath is set, which it is not by default -- so
    # a repository shipping an `ix.bat` at its root would own this server
    # outright, no metacharacters required. Passing `path=` does NOT prevent it:
    # CPython inserts os.curdir whenever the command has no directory part,
    # explicit path or not (verified on 3.13 -- it still returns `.\ix.BAT`).
    #
    # What does prevent it is refusing the result: a PATH hit is absolute, the
    # curdir hit is not.
    executable = shutil.which("ix", path=os.environ.get("PATH"))
    if executable is not None and not os.path.isabs(executable):
        _record_stderr(
            msg := (
                f"refusing to run {executable!r}: resolved from the working "
                "directory rather than PATH. Remove it, or put the real ix ahead "
                "of it on PATH."
            )
        )
        return False, "", msg
    if executable is None:
        _record_stderr(msg := "ix CLI not found. Install it and ensure it is on PATH.")
        return False, "", msg
    unsafe = _unsafe_for_cmd_shim(executable, args)
    if unsafe:
        _record_stderr(
            msg := (
                f"refusing to run `ix {args[0] if args else ''}`: an argument "
                f"contains {unsafe}, which the Windows command processor would act "
                "on rather than pass to the CLI. Re-run without those characters "
                "-- for a symbol, name it without them; for a search, simplify the "
                "pattern."
            )
        )
        return False, "", msg
    try:
        r = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        _record_stderr(r.stderr)
        return r.returncode == 0, r.stdout, r.stderr
    # ValueError too: an embedded NUL raises it out of subprocess on every
    # platform, and it is not an OSError.
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        _record_stderr(detail := str(exc))
        return False, "", detail


def _parse(text: str) -> object:
    text = text.strip()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return json.loads(text[i:])
            except json.JSONDecodeError:
                pass
    return None


def _json(args: list[str], timeout: int = 15) -> object:
    """Run an ix query and parse its output as JSON.

    `--format json` is requested rather than assumed. This parsed stdout as JSON
    while asking for nothing, so it was reading the human-oriented text output --
    _parse would find the first `{` or `[` in a rendered table and either fail or,
    worse, succeed on a fragment. Every subcommand reached from here accepts the
    flag; the ones that do not take --format at all (config, init, reset, upgrade,
    view, watch) are not exposed as tools.
    """
    ok, stdout, _ = _run([*args, "--format", "json"], timeout)
    return _parse(stdout) if ok else None


def _ok(data: object) -> str:
    return json.dumps(data, indent=2) if data is not None else json.dumps({})


def _err(tool: str, cmd: str, stderr: str = "") -> str:
    detail = (stderr or _recorded_stderr()).strip()
    msg = f"{cmd} failed: {detail}" if detail else f"{cmd} failed without output"
    return json.dumps({"error": msg, "tool": tool})


def _read(tool: str, cmd: str, args: list[str], timeout: int = 15) -> str:
    """Run a read command, preferring `--format llm` where the CLI supports it.

    These tools parsed JSON only to hand it straight back to the model through
    `_ok`, which re-serialises it with `indent=2` — so the round trip made the
    payload *larger* than it arrived. `--format llm` is the same content
    rendered 2-4x smaller.

    The fast-path is gated per command on the installed CLI's version, because
    `--format llm` landed tier by tier and an older `ix` answers it with human
    text rather than an error (see mcp/ix_llm.py). Anything that is not a
    confident success — unsupported command, old CLI, failed probe, empty
    output, an `error code=` record — returns None there and lands on the JSON
    path below, unchanged.

    Pro commands do come through here -- `ix_decisions` calls this -- and are
    kept off the fast-path by their absence from the version table, not by
    avoiding this helper. `@ix/pro` ships no llm renderer at any version, so
    the omission is permanent rather than a floor waiting to be met.
    """
    fast = try_llm(args, _run, timeout=timeout)
    if fast is not None:
        return fast
    data = _json(args, timeout)
    if data is None:
        return _err(tool, cmd)
    return _ok(data)


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def ix_health() -> str:
    """Check whether the ix CLI is available and the graph is ready."""
    if not shutil.which("ix"):
        return json.dumps({"error": "ix CLI not found. Install it and ensure it is on PATH.", "graph_ready": False})
    # No "ix" here any more: _run prepends it. This tool was the only one that
    # passed it, which is why it was the only one that worked.
    ok, stdout, stderr = _run(["--version"], timeout=5)
    if not ok:
        return json.dumps({"error": f"ix health probe failed: {stderr.strip()}", "graph_ready": False})
    version = stdout.strip().split()[0] if stdout.strip() else "unknown"
    return json.dumps({"version": version, "graph_ready": True})


@mcp.tool()
def ix_briefing() -> str:
    """Load the ix Pro session briefing for current goals, plans, and recent decisions."""
    data = _json(["briefing"])
    if data is None:
        return json.dumps({"error": "ix briefing failed or requires ix Pro"})
    return _ok(data)


@mcp.tool()
def ix_locate(symbol: str) -> str:
    """Resolve a symbol to its canonical graph-backed target."""
    return _read("ix_locate", f"ix locate {symbol}", ["locate", symbol])


@mcp.tool()
def ix_text(
    pattern: str,
    limit: int = 20,
    path: Optional[str] = None,
    language: Optional[str] = None,
) -> str:
    """Search text across the indexed repository and return ranked hits."""
    args = ["text", pattern, "--limit", str(limit)]
    if path:
        args += ["--path", path]
    if language:
        args += ["--language", language]
    return _read("ix_text", f"ix text {pattern}", args)


@mcp.tool()
def ix_impact(target: str) -> str:
    """Analyze the blast radius of a symbol or file — risk level and what depends on it.

    Field names are deliberately not promised here. On a current CLI this returns
    the compact llm rendering, which spells risk as `risk=` and omits a
    recommended-action field entirely; on an older one it returns the JSON
    object with `risk_level`/`dependents`/`recommended_action`. A docstring is
    the model's contract, so naming keys only one of the two paths produces is a
    promise this tool cannot keep.
    """
    return _read("ix_impact", f"ix impact {target}", ["impact", target])


@mcp.tool()
def ix_map(file: Optional[str] = None) -> str:
    """Ingest a file into the graph (ix map <file>) or run a full architecture map (ix map)."""
    # --format json like every other tool. Without it this asked for the rendered
    # view -- which for `map` is explicitly truncated (--max-items defaults to 10)
    # -- and then ran _parse over the ASCII art, so it reliably fell through to
    # {"raw": ...} and handed the model unstructured, silently partial text. It
    # was the last tool still doing what #14 was filed about.
    args = ["map", file, "--format", "json"] if file else ["map", "--format", "json"]
    ok, stdout, stderr = _run(args, timeout=60)
    if not ok:
        return _err("ix_map", f"ix map{' ' + file if file else ''}", stderr)
    return _ok(_parse(stdout) or {"raw": stdout.strip()})


@mcp.tool()
def ix_overview(target: str) -> str:
    """Return a structural overview of a symbol or file — its shape and where it sits.

    The llm rendering is a compressed record form; it does not reproduce every
    key the JSON object carries.
    """
    return _read("ix_overview", f"ix overview {target}", ["overview", target])


@mcp.tool()
def ix_read(symbol: str) -> str:
    """Read the source content of a symbol via the graph (bounds raw file reads to graph-known symbols)."""
    return _read("ix_read", f"ix read {symbol}", ["read", symbol])


@mcp.tool()
def ix_diff(
    from_rev: int,
    to_rev: int,
    target: Optional[str] = None,
    summary: bool = False,
) -> str:
    """Show the structural diff between two graph revisions, optionally scoped to a file or symbol."""
    args = ["diff", str(from_rev), str(to_rev)]
    if target:
        args.append(target)
    if summary:
        args.append("--summary")
    return _read("ix_diff", f"ix diff {from_rev}..{to_rev}", args)


@mcp.tool()
def ix_callers(symbol: str) -> str:
    """List entities that call a symbol (incoming call edges)."""
    return _read("ix_callers", f"ix callers {symbol}", ["callers", symbol])


@mcp.tool()
def ix_callees(symbol: str) -> str:
    """List entities called by a symbol (outgoing call edges)."""
    return _read("ix_callees", f"ix callees {symbol}", ["callees", symbol])


@mcp.tool()
def ix_imported_by(symbol: str) -> str:
    """List files or symbols that import a given symbol (incoming import edges)."""
    return _read("ix_imported_by", f"ix imported-by {symbol}", ["imported-by", symbol])


@mcp.tool()
def ix_imports(symbol: str) -> str:
    """List symbols or files imported by a given symbol (outgoing import edges)."""
    return _read("ix_imports", f"ix imports {symbol}", ["imports", symbol])


@mcp.tool()
def ix_depends(symbol: str, depth: int = 2) -> str:
    """Show the downstream dependency graph for a symbol up to a given depth (default 2)."""
    return _read("ix_depends", f"ix depends {symbol}", ["depends", symbol, "--depth", str(depth)])


@mcp.tool()
def ix_trace(symbol: str, to: Optional[str] = None) -> str:
    """Trace execution paths through a symbol — upstream callers and downstream callees."""
    args = ["trace", symbol]
    if to:
        args += ["--to", to]
    return _read("ix_trace", f"ix trace {symbol}", args)


@mcp.tool()
def ix_explain(symbol: str) -> str:
    """Explain a symbol's role, importance, and callers using graph data.

    Callees are reported as a count rather than a list: the llm rendering emits
    `edges callees=<n>`, and the names are only available under `--raw`, which
    this does not pass.
    """
    return _read("ix_explain", f"ix explain {symbol}", ["explain", symbol])


@mcp.tool()
def ix_rank(
    by: str = "dependents",
    kind: str = "class",
    top: int = 10,
    path: Optional[str] = None,
) -> str:
    """Rank symbols by a quality metric (dependents, callers, importers, members) to surface hotspots."""
    args = ["rank", "--by", by, "--kind", kind, "--top", str(top)]
    if path:
        args += ["--path", path]
    return _read("ix_rank", "ix rank", args)


@mcp.tool()
def ix_inventory(path: str, kind: str = "file") -> str:
    """List files or symbols within a repository path scope."""
    return _read("ix_inventory", f"ix inventory {path}", ["inventory", "--kind", kind, "--path", path])


@mcp.tool()
def ix_smells(path: Optional[str] = None, limit: int = 50) -> str:
    """Detect code quality smells across the graph — orphan files, high coupling, etc."""
    args = ["smells"]
    if path:
        args += ["--path", path]
    data = _json(args)
    if data is None:
        return _err("ix_smells", "ix smells")
    if isinstance(data, dict) and isinstance(data.get("candidates"), list):
        data["candidates"] = data["candidates"][:limit]
    return _ok(data)


@mcp.tool()
def ix_stats() -> str:
    """Return graph-wide ix statistics.

    The llm rendering reports node and edge counts and drops zero-valued
    categories, so this is a summary rather than the full JSON breakdown.
    """
    return _read("ix_stats", "ix stats", ["stats"])


@mcp.tool()
def ix_subsystems() -> str:
    """List graph-derived subsystems for top-level repository orientation."""
    return _read("ix_subsystems", "ix subsystems", ["subsystems"])


@mcp.tool()
def ix_decisions(path: Optional[str] = None) -> str:
    """List architecture decisions recorded in the graph, optionally scoped to a path."""
    args = ["decisions"]
    if path:
        args += ["--path", path]
    return _read("ix_decisions", "ix decisions", args)


@mcp.tool()
def ix_history(target: str) -> str:
    """Show the provenance/patch history for a file or symbol."""
    return _read("ix_history", f"ix history {target}", ["history", target])


if __name__ == "__main__":
    mcp.run(transport="stdio")
