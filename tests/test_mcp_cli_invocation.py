from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeFastMCP:
    def __init__(self, name: str) -> None:
        self.name = name

    def tool(self):
        return lambda function: function


def _load_server():
    mcp_module = types.ModuleType("mcp")
    mcp_server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = _FakeFastMCP

    spec = importlib.util.spec_from_file_location(
        "ix_mcp_server", REPO_ROOT / "mcp" / "server.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "mcp": mcp_module,
            "mcp.server": mcp_server_module,
            "mcp.server.fastmcp": fastmcp_module,
        },
    ):
        spec.loader.exec_module(module)
    return module


class McpCliInvocationTest(unittest.TestCase):
    def test_all_tools_invoke_ix_with_expected_arguments(self) -> None:
        server = _load_server()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_path = temp_path / "argv.jsonl"
            marker_path = temp_path / "shell-was-invoked"
            fake_ix = temp_path / "ix"
            fake_ix.write_text(
                """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_IX_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")

if sys.argv[1:] == ["--version"]:
    print("0.8.1")
else:
    print(json.dumps({"argv": sys.argv[1:]}))
""",
                encoding="utf-8",
            )
            fake_ix.chmod(0o755)

            unsafe_symbol = f"Widget; touch {marker_path}"
            original_path = os.environ.get("PATH", "")
            environment = {
                "FAKE_IX_LOG": str(log_path),
                "PATH": f"{temp_path}{os.pathsep}{original_path}",
            }
            with patch.dict(os.environ, environment):
                results = [
                    server.ix_health(),
                    server.ix_briefing(),
                    server.ix_locate(unsafe_symbol),
                    server.ix_text("needle", limit=7, path="src", language="python"),
                    server.ix_impact("Widget"),
                    server.ix_map(),
                    server.ix_overview("Widget"),
                    server.ix_read("Widget"),
                    server.ix_diff(3, 8, target="Widget", summary=True),
                    server.ix_callers("Widget"),
                    server.ix_callees("Widget"),
                    server.ix_imported_by("Widget"),
                    server.ix_imports("Widget"),
                    server.ix_depends("Widget", depth=2),
                    server.ix_trace("Widget", to="render"),
                    server.ix_explain("Widget"),
                    server.ix_rank(
                        by="callers", kind="function", top=5, path="src"
                    ),
                    server.ix_inventory("src", kind="function"),
                    server.ix_smells(path="src", limit=10),
                    server.ix_stats(),
                    server.ix_subsystems(),
                    server.ix_decisions(path="src"),
                    server.ix_history("Widget"),
                ]

            expected = [
                ["--version"],
                ["briefing", "--format", "json"],
                ["locate", unsafe_symbol, "--format", "json"],
                [
                    "text",
                    "needle",
                    "--limit",
                    "7",
                    "--path",
                    "src",
                    "--language",
                    "python",
                    "--format",
                    "json",
                ],
                ["impact", "Widget", "--format", "json"],
                ["map"],
                ["overview", "Widget", "--format", "json"],
                ["read", "Widget", "--format", "json"],
                [
                    "diff",
                    "3",
                    "8",
                    "Widget",
                    "--summary",
                    "--format",
                    "json",
                ],
                ["callers", "Widget", "--format", "json"],
                ["callees", "Widget", "--format", "json"],
                ["imported-by", "Widget", "--format", "json"],
                ["imports", "Widget", "--format", "json"],
                ["depends", "Widget", "--depth", "2", "--format", "json"],
                ["trace", "Widget", "--to", "render", "--format", "json"],
                ["explain", "Widget", "--format", "json"],
                [
                    "rank",
                    "--by",
                    "callers",
                    "--kind",
                    "function",
                    "--top",
                    "5",
                    "--path",
                    "src",
                    "--format",
                    "json",
                ],
                [
                    "inventory",
                    "--kind",
                    "function",
                    "--path",
                    "src",
                    "--format",
                    "json",
                ],
                ["smells", "--path", "src", "--format", "json"],
                ["stats", "--format", "json"],
                ["subsystems", "--format", "json"],
                ["decisions", "--path", "src", "--format", "json"],
                ["history", "Widget", "--format", "json"],
            ]
            actual = [json.loads(line) for line in log_path.read_text().splitlines()]

            self.assertEqual(expected, actual)
            self.assertEqual(23, len(results))
            self.assertFalse(marker_path.exists())
            self.assertTrue(all("error" not in json.loads(result) for result in results))


if __name__ == "__main__":
    unittest.main()
