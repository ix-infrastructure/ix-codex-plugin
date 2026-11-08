#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""Guidance contracts for the plugin's documented `ix` commands.

Everything here is hermetic: no test in this file runs `ix`. The helpers below
are shared with real_ix_contract.py, whose CliHelpContract checks the same
mentions against a released CLI's --help in the real-ix CI job.
"""

from __future__ import annotations

import ast
import re
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "plugins" / "ix-memory" / "skills"
FLAGS = re.compile(r"--[a-z][a-z-]*")
COMMAND = re.compile(r"(?<![\w$-])ix\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?")
GROUPS = {"bug", "plan", "mcp", "data"}
# Options the CLI rejects a command without. real_ix_contract.py proves each
# entry against the released CLI, so a stale entry fails there.
REQUIRED_OPTIONS: dict[tuple[str, ...], frozenset[str]] = {
    ("inventory",): frozenset({"--kind"}),
    ("rank",): frozenset({"--by", "--kind"}),
}


@dataclass(frozen=True)
class Mention:
    command: tuple[str, ...]
    flags: frozenset[str]
    source: str
    forbidden: bool = False


def mentions(text: str, source: str) -> list[Mention]:
    """Read inline code and fenced examples, including negative flag claims."""
    found = []
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        snippets = ([line] if re.match(r"^\s*ix\s+", line) else []) if fenced else re.findall(r"`([^`]+)`", line)
        # Flags outside a command's inline span still qualify that command:
        # e.g. `ix callers` accepts `--path`.
        detached = set(FLAGS.findall(" ".join(
            snippet for snippet in snippets if not COMMAND.search(snippet)
        )))
        forbidden = bool(re.search(r"(?:does|do) not (?:accept|support)|there is no", line))
        if detached and not any(COMMAND.search(snippet) for snippet in snippets):
            # Recovery and direction instructions often follow a fenced example.
            if re.search(r"multiple matches|ambiguous|disambiguat", line, re.IGNORECASE):
                found.append(Mention(("locate",), frozenset(detached), f"{source}:{number}"))
            elif re.search(r"\btrace\b", line, re.IGNORECASE):
                found.append(Mention(("trace",), frozenset(detached), f"{source}:{number}"))
        for snippet in snippets:
            matches = list(COMMAND.finditer(snippet))
            for index, match in enumerate(matches):
                command = (match[1],)
                if match[1] in GROUPS and match[2]:
                    command += (match[2],)
                end = matches[index + 1].start() if index + 1 < len(matches) else len(snippet)
                flags = set(FLAGS.findall(snippet[match.end(1):end])) | detached
                found.append(Mention(command, frozenset(flags), f"{source}:{number}", forbidden))
    return found


def plugin_mentions() -> list[Mention]:
    paths = [REPO / name for name in ("AGENTS.md", "README.md", "hooks.md", "PLUGIN_SPEC.md")]
    paths += sorted((REPO / "agents").glob("*.md"))
    paths += sorted(SKILLS.glob("*/SKILL.md"))
    found = []
    for path in paths:
        found.extend(mentions(path.read_text(encoding="utf-8"), str(path.relative_to(REPO))))
    for path in sorted((REPO / ".codex" / "hooks").glob("*.py")):
        source = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.extend(mentions(node.value, f"{source}:{node.lineno}"))
            elif isinstance(node, ast.List) and len(node.elts) >= 2:
                values = [item.value for item in node.elts if isinstance(item, ast.Constant)
                          and isinstance(item.value, str)]
                if values[:1] == ["ix"]:
                    found.extend(mentions("`" + " ".join(values) + "`", f"{source}:{node.lineno}"))
    return found


def missing_required(item: Mention) -> frozenset[str]:
    """Required options a documented invocation leaves out.

    A bare name (`ix rank`, no flags) refers to the command rather than invoking
    it, and a negative claim is not an invocation either, so neither is held to
    the required options.
    """
    if item.forbidden or not item.flags:
        return frozenset()
    return REQUIRED_OPTIONS.get(item.command, frozenset()) - item.flags


def help_options(help_text: str) -> set[str]:
    """Use option declarations, never example text or descriptive prose."""
    section = help_text.split("Options:\n", 1)[-1]
    section = re.split(r"\n(?:Commands|Examples):", section, maxsplit=1)[0]
    return set(re.findall(r"^\s+(?:-\w, )?(--[a-z][a-z-]*)\b", section, re.MULTILINE))


def is_pro_stub_help(help_text: str, command: tuple[str, ...]) -> bool:
    """True for the OSS CLI's placeholder for a Pro command.

    Without @ix/pro every Pro command is registered as a stub that swallows its
    arguments (`[args...]`) and declares only --help, so `ix plan list --help`
    prints the help of the `plan` stub. Its options say nothing about the real
    command's, so documented Pro flags can only be checked where Pro is installed.
    """
    usage = f"Usage: ix {command[0]} [options] [args...]\n"
    return help_text.startswith(usage) and help_options(help_text) == {"--help"}


def static_guidance() -> str:
    tree = ast.parse((REPO / ".codex" / "hooks" / "session_start.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_STATIC_GUIDANCE" for target in node.targets
        ):
            return "\n".join(ast.literal_eval(node.value.args[0]))
    raise AssertionError("_STATIC_GUIDANCE not found")


class PluginGuidanceTest(unittest.TestCase):
    def test_static_guidance_is_under_600_bytes(self) -> None:
        self.assertLess(len(static_guidance().encode("utf-8")), 600)

    def test_static_guidance_has_triggers_and_recovery(self) -> None:
        text = static_guidance()
        for trigger in ("ix locate", "ix search", "rg", "grep", "ix callers", "ix impact",
                        "before editing", "--pick", "--path", "--kind", "partial name",
                        "new or misspelled", "symbols you just wrote", "$ix-impact before edits"):
            with self.subTest(trigger=trigger):
                self.assertIn(trigger, text)

    def test_static_guidance_names_the_workflow_skills(self) -> None:
        text = static_guidance()
        for skill in ("ix-understand", "ix-investigate", "ix-impact", "ix-debug",
                      "ix-architecture", "ix-docs", "ix-plan"):
            with self.subTest(skill=skill):
                self.assertTrue((SKILLS / skill / "SKILL.md").is_file())
                self.assertIn("$" + skill, text)

    def test_all_skill_descriptions_start_with_use_when(self) -> None:
        skills = sorted(SKILLS.glob("*/SKILL.md"))
        self.assertTrue(skills)
        for path in skills:
            with self.subTest(skill=path.parent.name):
                frontmatter = path.read_text(encoding="utf-8").split("---", 2)[1]
                description = re.search(r"^description: (.+)$", frontmatter, re.MULTILINE)
                self.assertIsNotNone(description)
                self.assertTrue(description[1].startswith("Use when "), description[1])

    def test_command_extraction_preserves_unknown_commands_and_flags(self) -> None:
        text = "`ix invented <symbol> --invented`\n```bash\nix plan list --format json\n```"
        result = mentions(text, "example.md")
        self.assertEqual([("invented",), ("plan", "list")], [item.command for item in result])
        self.assertEqual(frozenset({"--invented"}), result[0].flags)

    def test_negative_claims_and_skill_flags_are_distinct(self) -> None:
        result = mentions(
            "`ix locate` does not accept `--limit`\n"
            "`$ix-docs <target> --full`\n"
            "`ix callers` and `ix callees` accept `--path`", "example.md"
        )
        self.assertEqual(3, len(result))
        self.assertTrue(result[0].forbidden)
        self.assertEqual(frozenset({"--limit"}), result[0].flags)
        self.assertEqual([frozenset({"--path"})] * 2, [item.flags for item in result[1:]])

    def test_help_parser_ignores_examples_and_option_descriptions(self) -> None:
        text = "Options:\n  --path <path>  Like --invented\n  -h, --help  Help\nExamples:\n  ix locate x --limit 5"
        self.assertEqual({"--path", "--help"}, help_options(text))

    def test_separate_recovery_and_trace_flags_are_validated(self) -> None:
        result = mentions("If ambiguous: use `--pick N` or `--path`.\n"
                          "One trace only: choose `--upstream` or `--downstream`.", "example.md")
        self.assertEqual([("locate",), ("trace",)], [item.command for item in result])
        self.assertEqual(frozenset({"--upstream", "--downstream"}), result[1].flags)

    def test_documented_invocations_supply_required_options(self) -> None:
        mentioned = plugin_mentions()
        for command in REQUIRED_OPTIONS:
            with self.subTest(command=" ".join(command)):
                self.assertTrue(any(item.command == command and item.flags for item in mentioned))
        for item in mentioned:
            with self.subTest(source=item.source, command=" ".join(item.command)):
                self.assertFalse(missing_required(item), item)

    def test_missing_required_option_detection(self) -> None:
        bad = mentions("`ix inventory --path <file>`\n```bash\nix rank --by dependents --top 5\n```",
                       "example.md")
        self.assertEqual([frozenset({"--kind"})] * 2, [missing_required(item) for item in bad])
        ok = mentions("`ix rank --by callers --kind function`, see `ix rank`.\n"
                      "`ix inventory` does not accept `--limit`", "example.md")
        self.assertEqual([frozenset()] * 3, [missing_required(item) for item in ok])

    def test_pro_stub_help_is_told_apart_from_real_help(self) -> None:
        # Verbatim from ix 0.12.0 without @ix/pro: `ix plan list --help`.
        stub = ("Usage: ix plan [options] [args...]\n\nManage plans and plan tasks\n\n"
                "Options:\n  -h, --help  display help for command\n")
        self.assertTrue(is_pro_stub_help(stub, ("plan", "list")))
        self.assertTrue(is_pro_stub_help(stub.replace("plan", "decisions", 1), ("decisions",)))
        real = "Usage: ix plan list [options]\n\nOptions:\n  --format <fmt>  Format\n  -h, --help  Help\n"
        self.assertFalse(is_pro_stub_help(real, ("plan", "list")))
        # A real command that happens to take no options is not a stub.
        bare = "Usage: ix stats [options]\n\nOptions:\n  -h, --help  Help\n"
        self.assertFalse(is_pro_stub_help(bare, ("stats",)))
        self.assertFalse(is_pro_stub_help(stub, ("bug", "list")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
