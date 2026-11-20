#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""The top-level documents say what the plugin does today.

`hooks.md`, `README.md` and `AGENTS.md` describe the current plugin. The other
top-level Markdown files are earlier plans and test logs; each must open with a
"Superseded" note so nobody reads them as the current design. Relative links in
any tracked Markdown file must point at a file inside this repository.

Hermetic: nothing here runs `ix` or the installer.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CURRENT_DOCS = {"README.md", "AGENTS.md", "hooks.md"}
LINK = re.compile(r"\]\(([^)\s]+)\)")


def section(text: str, heading: str) -> str:
    """The body of the `## heading` section, up to the next `## ` heading."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError(f"no '## {heading}' section")
    return match.group(1)


def markdown_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=REPO, capture_output=True, text=True, check=False
    )
    if listed.returncode == 0 and listed.stdout.strip():
        return [REPO / line for line in listed.stdout.splitlines()]
    return [path for path in REPO.rglob("*.md") if ".git" not in path.parts]


class HooksDocTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (REPO / "hooks.md").read_text(encoding="utf-8")

    def test_mcp_section_names_the_ix_cli_server(self) -> None:
        body = section(self.text, "MCP Tools")
        self.assertIn("`ix mcp install --host codex`", body)
        self.assertIn("`ix mcp`", body)

    def test_no_claim_of_a_pending_python_mcp_server(self) -> None:
        for stale in ("Python MCP server", "deferred (Phase 3", "not yet implemented"):
            self.assertNotIn(stale, self.text)

    def test_agent_playbooks_are_described_as_reference_text(self) -> None:
        body = section(self.text, "Agent Playbooks")
        self.assertIn("reference text", body)
        self.assertNotIn("not yet verified", body)


class ReadmeAgentsTest(unittest.TestCase):
    def test_readme_says_no_installer_mode_installs_the_playbooks(self) -> None:
        body = section((REPO / "README.md").read_text(encoding="utf-8"), "Agent Playbooks")
        self.assertIn("reference text", body)
        self.assertIn("No installer mode copies", body)

    def test_installer_does_not_copy_agents(self) -> None:
        # If an installer mode ever starts copying agents/, the README and
        # hooks.md wording above must change with it.
        source = (REPO / "scripts" / "install_codex_integration.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"[\"'/]agents[\"'/]")


class SupersededPlansTest(unittest.TestCase):
    def test_every_other_top_level_doc_opens_with_a_superseded_note(self) -> None:
        others = sorted(path for path in REPO.glob("*.md") if path.name not in CURRENT_DOCS)
        self.assertTrue(others)
        for path in others:
            with self.subTest(doc=path.name):
                head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:8])
                self.assertRegex(head, r"\*\*Superseded")

    def test_relative_links_stay_inside_the_repository_and_resolve(self) -> None:
        root = REPO.resolve()
        for path in markdown_files():
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                for target in LINK.findall(line):
                    if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                        continue
                    resolved = (path.parent / target.split("#", 1)[0]).resolve()
                    with self.subTest(link=f"{path.relative_to(REPO)}:{number} -> {target}"):
                        self.assertTrue(resolved.is_relative_to(root), "link leaves the repository")
                        self.assertTrue(resolved.exists(), "link target is missing")


if __name__ == "__main__":
    unittest.main()
