#!/usr/bin/env python3
"""ix-memory MCP server — stdio transport, Python FastMCP.

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
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ix-memory")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(args: list[str], timeout: int = 15) -> tuple[bool, str, str]:
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
        return r.returncode == 0, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", str(exc)


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
    ok, stdout, _ = _run(args, timeout)
    return _parse(stdout) if ok else None


def _ok(data: object) -> str:
    return json.dumps(data, indent=2) if data is not None else json.dumps({})


def _err(tool: str, cmd: str, stderr: str = "") -> str:
    detail = stderr.strip()
    msg = f"{cmd} failed: {detail}" if detail else f"{cmd} failed without output"
    return json.dumps({"error": msg, "tool": tool})


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def ix_health() -> str:
    """Check whether the ix CLI is available and the graph is ready."""
    if not shutil.which("ix"):
        return json.dumps({"error": "ix CLI not found. Install it and ensure it is on PATH.", "graph_ready": False})
    ok, stdout, stderr = _run(["ix", "--version"], timeout=5)
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
    data = _json(["locate", symbol])
    if data is None:
        return _err("ix_locate", f"ix locate {symbol}")
    return _ok(data)


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
    data = _json(args)
    if data is None:
        return _err("ix_text", f"ix text {pattern}")
    return _ok(data)


@mcp.tool()
def ix_impact(target: str) -> str:
    """Analyze the blast radius of a symbol or file — returns risk_level, dependents, and recommended_action."""
    data = _json(["impact", target])
    if data is None:
        return _err("ix_impact", f"ix impact {target}")
    return _ok(data)


@mcp.tool()
def ix_map(file: Optional[str] = None) -> str:
    """Ingest a file into the graph (ix map <file>) or run a full architecture map (ix map)."""
    args = ["map", file] if file else ["map"]
    ok, stdout, stderr = _run(args, timeout=60)
    if not ok:
        return _err("ix_map", f"ix map{' ' + file if file else ''}", stderr)
    return _ok(_parse(stdout) or {"raw": stdout.strip()})


@mcp.tool()
def ix_overview(target: str) -> str:
    """Return a structural overview of a symbol or file — children, key items, and hierarchy position."""
    data = _json(["overview", target])
    if data is None:
        return _err("ix_overview", f"ix overview {target}")
    return _ok(data)


@mcp.tool()
def ix_read(symbol: str) -> str:
    """Read the source content of a symbol via the graph (bounds raw file reads to graph-known symbols)."""
    data = _json(["read", symbol])
    if data is None:
        return _err("ix_read", f"ix read {symbol}")
    return _ok(data)


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
    data = _json(args)
    if data is None:
        return _err("ix_diff", f"ix diff {from_rev}..{to_rev}")
    return _ok(data)


@mcp.tool()
def ix_callers(symbol: str) -> str:
    """List entities that call a symbol (incoming call edges)."""
    data = _json(["callers", symbol])
    if data is None:
        return _err("ix_callers", f"ix callers {symbol}")
    return _ok(data)


@mcp.tool()
def ix_callees(symbol: str) -> str:
    """List entities called by a symbol (outgoing call edges)."""
    data = _json(["callees", symbol])
    if data is None:
        return _err("ix_callees", f"ix callees {symbol}")
    return _ok(data)


@mcp.tool()
def ix_imported_by(symbol: str) -> str:
    """List files or symbols that import a given symbol (incoming import edges)."""
    data = _json(["imported-by", symbol])
    if data is None:
        return _err("ix_imported_by", f"ix imported-by {symbol}")
    return _ok(data)


@mcp.tool()
def ix_imports(symbol: str) -> str:
    """List symbols or files imported by a given symbol (outgoing import edges)."""
    data = _json(["imports", symbol])
    if data is None:
        return _err("ix_imports", f"ix imports {symbol}")
    return _ok(data)


@mcp.tool()
def ix_depends(symbol: str, depth: int = 2) -> str:
    """Show the downstream dependency graph for a symbol up to a given depth (default 2)."""
    data = _json(["depends", symbol, "--depth", str(depth)])
    if data is None:
        return _err("ix_depends", f"ix depends {symbol}")
    return _ok(data)


@mcp.tool()
def ix_trace(symbol: str, to: Optional[str] = None) -> str:
    """Trace execution paths through a symbol — upstream callers and downstream callees."""
    args = ["trace", symbol]
    if to:
        args += ["--to", to]
    data = _json(args)
    if data is None:
        return _err("ix_trace", f"ix trace {symbol}")
    return _ok(data)


@mcp.tool()
def ix_explain(symbol: str) -> str:
    """Explain a symbol's role, importance, callers, and callees using graph data."""
    data = _json(["explain", symbol])
    if data is None:
        return _err("ix_explain", f"ix explain {symbol}")
    return _ok(data)


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
    data = _json(args)
    if data is None:
        return _err("ix_rank", "ix rank")
    return _ok(data)


@mcp.tool()
def ix_inventory(path: str, kind: str = "file") -> str:
    """List files or symbols within a repository path scope."""
    data = _json(["inventory", "--kind", kind, "--path", path])
    if data is None:
        return _err("ix_inventory", f"ix inventory {path}")
    return _ok(data)


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
    """Return graph-wide ix statistics for files, symbols, and graph health."""
    data = _json(["stats"])
    if data is None:
        return _err("ix_stats", "ix stats")
    return _ok(data)


@mcp.tool()
def ix_subsystems() -> str:
    """List graph-derived subsystems for top-level repository orientation."""
    data = _json(["subsystems"])
    if data is None:
        return _err("ix_subsystems", "ix subsystems")
    return _ok(data)


@mcp.tool()
def ix_decisions(path: Optional[str] = None) -> str:
    """List architecture decisions recorded in the graph, optionally scoped to a path."""
    args = ["decisions"]
    if path:
        args += ["--path", path]
    data = _json(args)
    if data is None:
        return _err("ix_decisions", "ix decisions")
    return _ok(data)


@mcp.tool()
def ix_history(target: str) -> str:
    """Show the provenance/patch history for a file or symbol."""
    data = _json(["history", target])
    if data is None:
        return _err("ix_history", f"ix history {target}")
    return _ok(data)


if __name__ == "__main__":
    mcp.run(transport="stdio")
