#!/usr/bin/env python3
"""The `--format llm` fast-path and, mostly, the version gate in front of it.

`ix` does not validate `--format`. Every renderer is
`if json: ... elif llm: ... else: text`, so an unrecognised value falls through
to human-readable text and exits 0. That is what makes the whole change safe —
there is no version of `ix` on which asking for `llm` breaks — and it is also
what makes a wrong gate dangerous: the CLI answers with prose instead of an
error, and nothing raises. Most of what follows pins that boundary.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ix_llm = load_module(REPO_ROOT / "mcp" / "ix_llm.py", "ix_llm")


def fake_run(version: str, output: str = "", ok: bool = True):
    """A `_run` stand-in that reports `version` and returns `output` otherwise."""
    calls: list[list[str]] = []

    def run(args, timeout=15):
        calls.append(list(args))
        if args == ["--version"]:
            return True, version, ""
        return ok, output, "" if ok else "boom"

    run.calls = calls  # type: ignore[attr-defined]
    return run


class Semver(unittest.TestCase):
    def test_parses_a_plain_version(self) -> None:
        self.assertEqual((0, 9, 2), ix_llm.parse_semver("0.9.2"))

    def test_parses_a_decorated_version(self) -> None:
        # `ix --version` has printed a bare number, and could print more.
        self.assertEqual((0, 9, 2), ix_llm.parse_semver("ix 0.9.2 (linux-amd64)"))

    def test_ignores_a_prerelease_suffix(self) -> None:
        # 0.9.0-rc.1 is treated as 0.9.0. It predates every floor that matters,
        # and the alternative — full semver precedence — buys nothing here.
        self.assertEqual((0, 9, 0), ix_llm.parse_semver("0.9.0-rc.1"))

    def test_returns_none_for_junk(self) -> None:
        self.assertIsNone(ix_llm.parse_semver("unknown"))
        self.assertIsNone(ix_llm.parse_semver(""))


class Gate(unittest.TestCase):
    def setUp(self) -> None:
        ix_llm.reset_version_cache()

    def tearDown(self) -> None:
        ix_llm.reset_version_cache()

    def test_tier_one_command_is_allowed_from_070(self) -> None:
        self.assertTrue(ix_llm.supports_llm("stats", fake_run("0.7.0")))

    def test_tier_one_command_is_refused_below_070(self) -> None:
        self.assertFalse(ix_llm.supports_llm("stats", fake_run("0.6.0")))

    def test_tier_five_command_is_refused_between_070_and_092(self) -> None:
        """The reason the floors are per-command.

        `explain` and `read` only grew renderers in 0.9.2. On 0.9.1 they accept
        `--format llm` and return *text* — no error, exit 0 — so a single
        0.7.0 gate would forward prose to the model as though it were records.
        """
        for version in ("0.7.0", "0.8.1", "0.9.0", "0.9.1"):
            ix_llm.reset_version_cache()
            self.assertFalse(
                ix_llm.supports_llm("explain", fake_run(version)),
                f"explain must not use llm on {version}",
            )
            ix_llm.reset_version_cache()
            self.assertFalse(ix_llm.supports_llm("read", fake_run(version)))

    def test_tier_five_command_is_allowed_from_092(self) -> None:
        self.assertTrue(ix_llm.supports_llm("explain", fake_run("0.9.2")))
        ix_llm.reset_version_cache()
        self.assertTrue(ix_llm.supports_llm("read", fake_run("0.9.3")))

    def test_pro_commands_are_never_allowed(self) -> None:
        """`@ix/pro` declares only text|json — there is no llm renderer at any
        version, so no gate can let these through and none should."""
        for command in ("briefing", "decisions", "goals", "plan", "truth"):
            ix_llm.reset_version_cache()
            self.assertFalse(ix_llm.supports_llm(command, fake_run("99.0.0")), command)

    def test_an_unknown_command_is_refused(self) -> None:
        ix_llm.reset_version_cache()
        self.assertFalse(ix_llm.supports_llm("nonesuch", fake_run("99.0.0")))

    def test_an_unreadable_version_disables_the_fast_path(self) -> None:
        def run(args, timeout=15):
            return True, "not a version", ""

        self.assertFalse(ix_llm.supports_llm("stats", run))

    def test_a_failed_probe_disables_the_fast_path(self) -> None:
        def run(args, timeout=15):
            return False, "", "ix not found"

        self.assertFalse(ix_llm.supports_llm("stats", run))

    def test_a_raising_probe_disables_the_fast_path(self) -> None:
        def run(args, timeout=15):
            raise OSError("no such executable")

        self.assertFalse(ix_llm.supports_llm("stats", run))

    def test_the_version_is_probed_once(self) -> None:
        run = fake_run("0.9.2")
        for _ in range(5):
            ix_llm.supports_llm("stats", run)
        probes = [c for c in run.calls if c == ["--version"]]
        self.assertEqual(1, len(probes))

    def test_kill_switch(self) -> None:
        import os

        os.environ["IX_DISABLE_LLM_FORMAT"] = "1"
        try:
            self.assertFalse(ix_llm.supports_llm("stats", fake_run("0.9.2")))
        finally:
            del os.environ["IX_DISABLE_LLM_FORMAT"]

    def test_diff_content_stays_on_text(self) -> None:
        """docs/llm-format.md keeps `diff --content` on text: verbatim hunks
        have no record form. Guards a future edit, not a live call site."""
        self.assertTrue(ix_llm.supports_llm("diff", fake_run("0.9.2"), ["diff", "1", "5"]))
        ix_llm.reset_version_cache()
        self.assertFalse(
            ix_llm.supports_llm("diff", fake_run("0.9.2"), ["diff", "1", "5", "--content"])
        )


class TryLlm(unittest.TestCase):
    def setUp(self) -> None:
        ix_llm.reset_version_cache()

    def tearDown(self) -> None:
        ix_llm.reset_version_cache()

    def test_returns_text_and_asks_for_the_llm_format(self) -> None:
        run = fake_run("0.9.2", output="stats nodes=98979 edges=354283\n")
        text = ix_llm.try_llm(["stats"], run)
        self.assertEqual("stats nodes=98979 edges=354283", text)
        self.assertIn(["stats", "--format", "llm"], run.calls)

    def test_defers_when_the_cli_is_too_old(self) -> None:
        run = fake_run("0.6.0", output="stats nodes=1")
        self.assertIsNone(ix_llm.try_llm(["stats"], run))
        # And did not even try the command.
        self.assertNotIn(["stats", "--format", "llm"], run.calls)

    def test_defers_on_a_failed_invocation(self) -> None:
        self.assertIsNone(ix_llm.try_llm(["stats"], fake_run("0.9.2", ok=False)))

    def test_defers_on_empty_output(self) -> None:
        self.assertIsNone(ix_llm.try_llm(["stats"], fake_run("0.9.2", output="   \n")))

    def test_defers_on_an_error_record(self) -> None:
        """ix reports some failures as `error code=...` on stdout with exit 0.

        Forwarding that verbatim would hand the model an error line dressed as
        a result. Deferring keeps the error contract identical to the JSON path.
        """
        run = fake_run("0.9.2", output='error code=unknown_target message="No entity named X"')
        self.assertIsNone(ix_llm.try_llm(["locate", "X"], run))

    def test_defers_on_an_empty_argv(self) -> None:
        self.assertIsNone(ix_llm.try_llm([], fake_run("0.9.2")))

    def test_does_not_parse_the_output(self) -> None:
        # The contract is passthrough. Anything llm-shaped comes back verbatim,
        # including content that is not valid JSON and never has to be.
        payload = 'region id=cli label="Cli / Client" level=2\nregion id=srv parent=cli'
        run = fake_run("0.9.2", output=payload + "\n")
        self.assertEqual(payload, ix_llm.try_llm(["subsystems"], run))


class ServerWiring(unittest.TestCase):
    """The server must degrade rather than die if the module is missing."""

    def test_server_imports_try_llm_defensively(self) -> None:
        source = (REPO_ROOT / "mcp" / "server.py").read_text(encoding="utf-8")
        self.assertIn("from ix_llm import try_llm", source)
        self.assertIn("except ImportError", source)

    def test_installer_ships_both_files(self) -> None:
        # server.py imports ix_llm as a sibling; installing only server.py would
        # silently drop the fast-path on every install.
        source = (REPO_ROOT / "scripts" / "install_codex_integration.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"server.py", "ix_llm.py"', source)

    def test_pro_tools_do_not_reach_the_fast_path(self) -> None:
        # ix_decisions routes through _read like the rest; it is safe only
        # because `decisions` is absent from the table. Pin that.
        self.assertNotIn("decisions", ix_llm.LLM_MIN_VERSION)
        self.assertNotIn("briefing", ix_llm.LLM_MIN_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
