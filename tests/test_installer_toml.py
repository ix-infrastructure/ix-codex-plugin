#!/usr/bin/env python3
"""The installer's config.toml edit has to leave the file loadable.

`ensure_codex_hooks_enabled` used to test `"codex_hooks = true" in text` and,
failing that, append a second assignment to the end of the [features] table.
Against an existing `codex_hooks = false` that produced a duplicate key — which
no TOML parser accepts — so the installer exited 0 having made the user's config
unloadable. The same substring test read `# codex_hooks = true` in a comment as
already-enabled, and missed `codex_hooks=true` written without spaces.

Every case here therefore ends in a real `tomllib` parse rather than a string
assertion, and checks that unrelated tables survive the edit.

The pure-function cases come from #13; the on-disk round-trip and the
"exactly one assignment survives" check come from @Hiro-Chiba's #17, folded in
here because both PRs rewrite this same function.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    tomllib = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]

PROJECTS = '[projects."/tmp/example"]\ntrust_level = "trusted"\n'


def _load_installer():
    spec = importlib.util.spec_from_file_location(
        "ix_installer", REPO_ROOT / "scripts" / "install_codex_integration.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assignments_in_features(text: str) -> list[str]:
    """Non-comment `codex_hooks` assignments that belong to the [features] table."""
    found: list[str] = []
    table: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            table = stripped[1:-1].strip()
            continue
        if table == "features" and not stripped.startswith("#") and stripped.startswith("codex_hooks"):
            found.append(stripped)
    return found


CASES = [
    ("existing false", "[features]\ncodex_hooks = false\n\n" + PROJECTS),
    ("commented-out decoy", "[features]\n# codex_hooks = true\n\n" + PROJECTS),
    ("no spaces around =", "[features]\ncodex_hooks=false\n\n" + PROJECTS),
    ("no [features] table", PROJECTS),
    ("empty [features]", "[features]\n\n" + PROJECTS),
    # A config the *old* installer already corrupted. Refusing to touch it would
    # leave the user hand-editing TOML in order to run an installer.
    (
        "duplicate key from old bug",
        "[features]\ncodex_hooks = false\n\ncodex_hooks = true\n\n" + PROJECTS,
    ),
    # codex_hooks under some other table is a different key and must not count.
    (
        "key in another table",
        "[other]\ncodex_hooks = true\n\n[features]\nsomething = 1\n\n" + PROJECTS,
    ),
]


@unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
class InstallerTomlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.installer = _load_installer()

    def test_edit_produces_valid_toml_enabling_the_flag(self) -> None:
        for name, source in CASES:
            with self.subTest(case=name):
                updated = self.installer.enable_codex_hooks(source)
                self.assertIsNotNone(updated, "reported nothing to do")
                parsed = tomllib.loads(updated)
                self.assertIs(
                    parsed.get("features", {}).get("codex_hooks"),
                    True,
                    "codex_hooks should be enabled",
                )

    def test_unrelated_tables_survive_the_edit(self) -> None:
        for name, source in CASES:
            if "projects" not in source:
                continue
            with self.subTest(case=name):
                parsed = tomllib.loads(self.installer.enable_codex_hooks(source))
                self.assertEqual(
                    "trusted", parsed["projects"]["/tmp/example"]["trust_level"]
                )

    def test_a_correct_config_is_left_completely_alone(self) -> None:
        self.assertIsNone(
            self.installer.enable_codex_hooks("[features]\ncodex_hooks = true\n"),
            "rewrote a config that was already correct",
        )

    def test_on_disk_round_trip_leaves_exactly_one_assignment(self) -> None:
        """The file-level entry point, not just the pure function.

        Duplicate keys are what made the old output unloadable, so the count of
        surviving `codex_hooks =` lines is the property worth asserting directly
        — tomllib would reject a duplicate, but this says *why* it would.

        Scoped to [features]: a `codex_hooks` under any other table is a
        different key and must survive untouched, so counting across the whole
        file would fail the "key in another table" case for the wrong reason.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, (name, source) in enumerate(CASES):
                with self.subTest(case=name):
                    config = Path(temp_dir) / f"config-{index}.toml"
                    config.write_text(source, encoding="utf-8")

                    self.installer.ensure_codex_hooks_enabled(config)

                    updated = config.read_text(encoding="utf-8")
                    parsed = tomllib.loads(updated)
                    self.assertIs(parsed["features"]["codex_hooks"], True)

                    self.assertEqual(
                        ["codex_hooks = true"],
                        _assignments_in_features(updated),
                    )

    def test_on_disk_edit_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text("[features]\ncodex_hooks = false\n\n" + PROJECTS)

            self.installer.ensure_codex_hooks_enabled(config)
            once = config.read_text(encoding="utf-8")
            self.installer.ensure_codex_hooks_enabled(config)
            twice = config.read_text(encoding="utf-8")

            self.assertEqual(once, twice)
            # Stability alone does not discriminate: the old implementation was
            # accidentally idempotent, because its `"codex_hooks = true" in text`
            # test matched the duplicate line it had just written. This assertion
            # compared two identical *corrupt* files and passed. Parse it.
            self.assertIs(tomllib.loads(twice)["features"]["codex_hooks"], True)
            self.assertEqual(["codex_hooks = true"], _assignments_in_features(twice))

    def test_an_array_of_tables_ends_the_features_section(self) -> None:
        """`[[x]]` is a header too — keys under it are not [features] keys.

        Matching only `[x]` left it unrecognised, so a `codex_hooks` key inside
        the array was attributed to [features], collapsed as a duplicate and
        deleted. The result still parses, so the tomllib guard cannot catch it.
        """
        source = (
            "[features]\ncodex_hooks = false\n\n"
            '[[mcp_servers]]\nname = "a"\ncodex_hooks = "keep me"\n'
        )
        updated = self.installer.enable_codex_hooks(source)
        assert updated is not None
        parsed = tomllib.loads(updated)
        self.assertIs(parsed["features"]["codex_hooks"], True)
        self.assertEqual("keep me", parsed["mcp_servers"][0]["codex_hooks"])

    def test_configs_that_already_enable_it_another_way_are_left_alone(self) -> None:
        """Valid TOML the line rewriter cannot read must be a no-op, not an abort.

        Each of these is already `features.codex_hooks = true`. The rewriter sees
        no `[features]` *header*, so it would append one and produce a duplicate
        declaration — turning a config that was fine into a SystemExit telling
        the user to hand-edit TOML.
        """
        for label, source in [
            ("inline table", "features = { codex_hooks = true }\n"),
            ("dotted key", "features.codex_hooks = true\n"),
            ("quoted key", '[features]\n"codex_hooks" = true\n'),
            ("quoted header", '["features"]\ncodex_hooks = true\n'),
        ]:
            with self.subTest(label):
                self.assertIsNone(self.installer.enable_codex_hooks(source))

    def test_a_quoted_header_containing_brackets_ends_the_section(self) -> None:
        """`[` and `]` are legal in a quoted key, and Codex writes real paths."""
        source = (
            "[features]\ncodex_hooks = false\n\n"
            "[projects.'C:/repos/proj[old]']\ntrust_level = \"trusted\"\n"
            'codex_hooks = "keep me"\n'
        )
        updated = self.installer.enable_codex_hooks(source)
        assert updated is not None
        parsed = tomllib.loads(updated)
        self.assertIs(parsed["features"]["codex_hooks"], True)
        self.assertEqual(
            "keep me", parsed["projects"]["C:/repos/proj[old]"]["codex_hooks"]
        )

    def test_a_quoted_key_is_rewritten_not_duplicated(self) -> None:
        """TOML reads `"codex_hooks"` and `codex_hooks` as the same key."""
        for source in (
            '[features]\n"codex_hooks" = false\n',
            "[features]\n'codex_hooks' = false\n",
        ):
            with self.subTest(source):
                updated = self.installer.enable_codex_hooks(source)
                assert updated is not None
                self.assertIs(tomllib.loads(updated)["features"]["codex_hooks"], True)
                self.assertEqual(["codex_hooks = true"], _assignments_in_features(updated))

    def test_a_features_subtable_is_not_mistaken_for_an_uneditable_spelling(self) -> None:
        """`[features.sub]` puts a dict at `features` with the key simply absent.

        Aborting on "features exists" would refuse a config the line rewriter
        handles perfectly well — appending `[features]` after a sub-table is
        legal TOML. Only the flag being unreachable should stop the edit.
        """
        for source in (
            "[features.sub]\nx = 1\n",
            '[mcp_servers.a]\ncmd = "x"\n\n[features.sub]\nx = 1\n',
        ):
            with self.subTest(source):
                updated = self.installer.enable_codex_hooks(source)
                assert updated is not None
                self.assertIs(tomllib.loads(updated)["features"]["codex_hooks"], True)

    def test_a_multiline_array_is_not_read_as_a_table_header(self) -> None:
        """`["a[1]"]` is both a valid quoted-key header and an array element.

        Only the nesting depth tells them apart, so a line-level pattern that
        accepts bracketed names has to track it — otherwise the array element
        ends the [features] section and the flag lands inside the literal.
        """
        for source in (
            '[features]\npats = [\n  ["a[1]"]\n]\ncodex_hooks = false\n',
            "[features]\np = [\n  [[1,2],[3,4]]\n]\ncodex_hooks = false\n",
        ):
            with self.subTest(source):
                updated = self.installer.enable_codex_hooks(source)
                assert updated is not None
                self.assertIs(tomllib.loads(updated)["features"]["codex_hooks"], True)

    def test_a_multiline_string_does_not_swallow_the_rest_of_the_file(self) -> None:
        """Quote state has to survive between lines, not just within one.

        A triple-quoted string containing an unbalanced `[` otherwise reads as
        an array that never closes, so every later line counts as part of a
        value: the existing assignment goes unseen and a second one is appended.
        """
        cases = {
            "inside [features]": (
                '[features]\nnote = """\nsee [1\n"""\ncodex_hooks = false\n'
            ),
            "before [features]": (
                'note = """\nsee [1\n"""\n\n[features]\ncodex_hooks = false\n'
            ),
            "literal triple quote": (
                "[features]\nnote = '''\na [ b\n'''\ncodex_hooks = false\n"
            ),
            "hash inside a string is not a comment": (
                '[features]\nnote = "a # b [ c"\ncodex_hooks = false\n'
            ),
        }
        for label, source in cases.items():
            with self.subTest(label):
                updated = self.installer.enable_codex_hooks(source)
                assert updated is not None
                self.assertIs(tomllib.loads(updated)["features"]["codex_hooks"], True)
                self.assertEqual(["codex_hooks = true"], _assignments_in_features(updated))

    def test_an_escaped_delimiter_does_not_close_a_multiline_string(self) -> None:
        r"""A multi-line *basic* string honours escapes, so `\"""` is not the end.

        Treating it as the end closes the string early, and the flag is then
        written into whichever table the scanner believes it is in. The result is
        valid TOML, so the write guard cannot catch it -- this is the failure
        mode that has to be tested rather than trusted.
        """
        quote = '"' * 3
        source = (
            "[features]\n"
            f"note = {quote}\n"
            f"a \\{quote} b\n"
            f"{quote}\n"
            "codex_hooks = false\n\n"
            '[projects."/tmp/example"]\n'
            'trust_level = "trusted"\n'
        )
        updated = self.installer.enable_codex_hooks(source)
        assert updated is not None
        parsed = tomllib.loads(updated)
        self.assertIs(parsed["features"]["codex_hooks"], True)
        self.assertEqual(["codex_hooks = true"], _assignments_in_features(updated))
        self.assertNotIn(
            "codex_hooks",
            parsed["projects"]["/tmp/example"],
            "the flag was written into the wrong table",
        )

        # A literal string has no escapes, so a backslash there must not skip.
        literal = "'" * 3
        source = (
            "[features]\n"
            f"note = {literal}\n"
            "C:\\repos [ x\n"
            f"{literal}\n"
            "codex_hooks = false\n"
        )
        updated = self.installer.enable_codex_hooks(source)
        assert updated is not None
        self.assertIs(tomllib.loads(updated)["features"]["codex_hooks"], True)

    def test_an_array_of_features_tables_aborts_about_the_installer(self) -> None:
        """`[[features]]` is not a table a `[features]` can be appended beside.

        Letting it fall through to the appender still aborts, but as "the result
        would not be valid TOML" — blaming the user's config for the installer's
        limits, which is the message this branch exists to replace.
        """
        for source in ("[[features]]\nx = 1\n", "[[ features ]]\nx = 1\n"):
            with self.subTest(source):
                with self.assertRaises(SystemExit) as caught:
                    self.installer.enable_codex_hooks(source)
                self.assertIn("does not edit", str(caught.exception))

    def test_features_defined_without_the_flag_is_still_editable(self) -> None:
        """`features` existing is not the same as the flag being unreachable.

        `[features.sub]` and `["features"]` both put a dict there; the first is a
        different table that a `[features]` may legally follow, and the second is
        the same table under another spelling, which the rewriter can edit
        directly. Only a direct assignment to `features` is beyond it.
        """
        for label, source in {
            "sub-table": "[features.sub]\nx = 1\n",
            "quoted header, other key": '["features"]\nother = 1\n',
            "quoted header, the flag": '["features"]\ncodex_hooks = false\n',
        }.items():
            with self.subTest(label):
                updated = self.installer.enable_codex_hooks(source)
                assert updated is not None
                self.assertIs(tomllib.loads(updated)["features"]["codex_hooks"], True)

        for label, source in {
            "inline table, other key": "features = { other = 1 }\n",
            "dotted, other key": "features.other = 1\n",
        }.items():
            with self.subTest(label):
                with self.assertRaises(SystemExit):
                    self.installer.enable_codex_hooks(source)

    def test_an_uneditable_spelling_aborts_with_a_message_about_itself(self) -> None:
        """Not "the result would not be valid TOML" — the input is fine."""
        for source in (
            "features = { codex_hooks = false }\n",
            "features.codex_hooks = false\n",
        ):
            with self.subTest(source):
                with self.assertRaises(SystemExit) as caught:
                    self.installer.enable_codex_hooks(source)
                self.assertIn("does not edit", str(caught.exception))

    def test_without_a_toml_parser_it_declines_rather_than_guesses(self) -> None:
        """On <3.11 with no tomli, appending [features] blind can corrupt."""
        original = self.installer.tomllib
        self.addCleanup(setattr, self.installer, "tomllib", original)
        self.installer.tomllib = None

        with self.assertRaises(SystemExit):
            self.installer.enable_codex_hooks("features = { codex_hooks = true }\n")

        # The forms it *can* read are still edited, and — the part that matters —
        # a config that never mentions `features` cannot collide with the table
        # about to be appended. Gating on "file is non-empty" instead would abort
        # on every 3.10 machine that has ever run Codex, and since install_hooks
        # runs before install_mcp that would block the server fix from shipping.
        for source in (
            "[features]\ncodex_hooks = false\n",
            '[mcp_servers.a]\ncmd = "x"\n',
            "# just a comment\n",
            "",
        ):
            with self.subTest(source):
                updated = self.installer.enable_codex_hooks(source)
                assert updated is not None
                self.assertIs(tomllib.loads(updated)["features"]["codex_hooks"], True)

    def test_a_bom_does_not_hide_the_features_table(self) -> None:
        """PowerShell 5.1's Set-Content writes one by default."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text(
                "[features]\ncodex_hooks = false\n\n" + PROJECTS, encoding="utf-8-sig"
            )
            self.installer.ensure_codex_hooks_enabled(config)
            text = config.read_text(encoding="utf-8-sig")
            self.assertIs(tomllib.loads(text)["features"]["codex_hooks"], True)
            self.assertEqual(["codex_hooks = true"], _assignments_in_features(text))

    def test_a_non_latin1_project_path_survives(self) -> None:
        """read_text() with no encoding uses the ANSI codepage on Windows."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text(
                '[features]\ncodex_hooks = false\n\n[projects."/tmp/あ"]\n'
                'trust_level = "trusted"\n',
                encoding="utf-8",
            )
            self.installer.ensure_codex_hooks_enabled(config)
            parsed = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertIs(parsed["features"]["codex_hooks"], True)
            self.assertIn("/tmp/あ", parsed["projects"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
