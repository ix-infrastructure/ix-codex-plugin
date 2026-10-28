#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""Graph lookups that ix answered with "here is why", not with results.

Since Ix #853 a graph lookup (`locate`, `overview`, `impact`, ...) exits 2 for an
ambiguous target (with `candidates`), 3 for one it could not find (with
`suggestions`) and 4 for a workspace that is not mapped -- all with a JSON record
on stdout. Exit 1 stays the real error. Before #853 all four exited 1 with the
same records, so a record on exit 1 is read the same way.

The hooks used to drop stdout on any non-zero exit, so the model never saw the
candidates it needed to retry with `--pick` / `--path`. These tests fake each
exit and check what reaches the model -- and that `status`, `briefing` and the
automatic map, which rely on "non-zero means no answer", are untouched.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".codex" / "hooks"


def _load():
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    sys.modules.pop("common", None)
    spec = importlib.util.spec_from_file_location("common", HOOKS_DIR / "common.py")
    common = importlib.util.module_from_spec(spec)
    sys.modules["common"] = common
    spec.loader.exec_module(common)
    return common


AMBIGUOUS = {
    "error": "ambiguous_target",
    "message": "'Widget' matches 3 entities",
    "candidates": [
        {"name": "Widget", "kind": "class", "path": "src/ui/widget.ts", "line": 12, "pick": 1},
        {"name": "Widget", "kind": "interface", "path": "src/api/types.ts", "line": 40, "pick": 2},
        {"name": "Widget", "kind": "function", "path": "lib/widget.py", "pick": 3},
    ],
}
# Candidates without ix's own `pick` numbers.
AMBIGUOUS_NO_PICK = dict(
    AMBIGUOUS,
    candidates=[{k: v for k, v in c.items() if k != "pick"} for c in AMBIGUOUS["candidates"]],
)
# `ix locate` keys its ambiguity record on `code`, with no `error` key.
LOCATE_AMBIGUOUS = {key: value for key, value in AMBIGUOUS.items() if key != "error"}
LOCATE_AMBIGUOUS["code"] = "ambiguous_target"
NOT_FOUND = {
    "error": "unresolved_target",
    "message": "No entity named 'Widgte'",
    "suggestions": [{"name": "Widget", "kind": "class", "path": "src/ui/widget.ts"}, "WidgetFactory"],
}
NOT_FOUND_BARE = {"error": "unresolved_target", "message": "No entity named 'zzz'", "suggestions": []}
NOT_MAPPED = {"error": "workspace_not_mapped", "message": "This workspace has not been mapped."}
REAL_ERROR = {"error": "backend_unreachable", "message": "connect ECONNREFUSED 127.0.0.1:1"}
TEXT_HITS = {"results": [{"path": "src/ui/widget.ts"}, {"path": "lib/widget.py"}]}


class ScriptedIx:
    """`ix <subcommand>` answers (exit, stdout) from a script; anything else is `{}`.

    An answer of TimeoutError raises subprocess.TimeoutExpired, as a hung ix would.
    """

    def __init__(self, **answers) -> None:
        self.answers = answers
        self.runs: list[list[str]] = []

    def run(self, argv, **kwargs):
        args = list(argv[1:])
        self.runs.append(args)
        answer = self.answers.get(args[0] if args else "", (0, "{}"))
        if answer is TimeoutError:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 0)
        code, stdout = answer
        if not isinstance(stdout, str):
            stdout = json.dumps(stdout)
        return subprocess.CompletedProcess(argv, code, stdout, "")


class LookupHelperTest(unittest.TestCase):
    def lookup(self, code, stdout, command="impact"):
        common = _load()
        fake = ScriptedIx(**{command: (code, stdout)})
        with patch.object(common.subprocess, "run", fake.run):
            return common.run_ix_lookup(["ix", command, "x", "--format", "json"])

    def test_new_exit_codes_are_answers(self) -> None:
        self.assertEqual(("ambiguous", AMBIGUOUS), self.lookup(2, AMBIGUOUS))
        self.assertEqual(("not_found", NOT_FOUND), self.lookup(3, NOT_FOUND))
        self.assertEqual(("not_mapped", NOT_MAPPED), self.lookup(4, NOT_MAPPED))
        self.assertEqual(("ambiguous", LOCATE_AMBIGUOUS), self.lookup(2, LOCATE_AMBIGUOUS, "locate"))

    def test_exit_0_is_a_result(self) -> None:
        self.assertEqual(("ok", {"riskLevel": "low"}), self.lookup(0, {"riskLevel": "low"}))

    def test_an_older_ix_exiting_1_with_the_record_is_read_the_same_way(self) -> None:
        self.assertEqual(("ambiguous", AMBIGUOUS), self.lookup(1, AMBIGUOUS))
        self.assertEqual(("not_found", NOT_FOUND), self.lookup(1, NOT_FOUND))
        self.assertEqual(("not_mapped", NOT_MAPPED), self.lookup(1, NOT_MAPPED))

    def test_real_errors_bad_json_and_timeouts_are_still_no_answer(self) -> None:
        cases = {
            "exit 1, other slug": (1, REAL_ERROR),
            "exit 1, plain text": (1, "error code=backend_unreachable message=down"),
            "exit 2, not JSON": (2, "Widget is ambiguous"),
            "exit 3, empty": (3, ""),
            "exit 4, a list": (4, "[1, 2]"),
            "exit 127": (127, NOT_MAPPED),
        }
        for label, (code, stdout) in cases.items():
            with self.subTest(label):
                self.assertEqual(("error", None), self.lookup(code, stdout))
        common = _load()
        with patch.object(common.subprocess, "run", ScriptedIx(impact=TimeoutError).run):
            self.assertEqual(("error", None), common.run_ix_lookup(["ix", "impact", "x"]))

    def test_run_ix_json_still_means_nothing_on_any_non_zero_exit(self) -> None:
        # status, briefing and the installer depend on it.
        common = _load()
        for code, stdout in ((2, AMBIGUOUS), (3, NOT_FOUND), (4, NOT_MAPPED), (1, NOT_MAPPED)):
            with self.subTest(code=code), patch.object(
                common.subprocess, "run", ScriptedIx(impact=(code, stdout)).run
            ):
                self.assertIsNone(common.run_ix_json(["ix", "impact", "x", "--format", "json"]))


class SearchPathTest(unittest.TestCase):
    def search(self, locate, text=(0, TEXT_HITS), pattern="Widget"):
        common = _load()
        fake = ScriptedIx(text=text, locate=locate)
        with patch.object(common.subprocess, "run", fake.run):
            return common.build_search_message(pattern, None)

    def test_exit_2_lists_candidates_with_pick_numbers(self) -> None:
        message = self.search((2, LOCATE_AMBIGUOUS))
        self.assertIn("'Widget' is ambiguous", message)
        self.assertIn("[1] Widget (class, src/ui/widget.ts:12)", message)
        self.assertIn("[2] Widget (interface, src/api/types.ts:40)", message)
        self.assertIn("[3] Widget (function, lib/widget.py)", message)
        self.assertIn("--pick <n>", message)
        self.assertIn("--path", message)
        self.assertIn("2 text hits", message)

    def test_exit_3_shows_the_suggestions(self) -> None:
        message = self.search((3, NOT_FOUND), text=(0, {"results": []}), pattern="Widgte")
        self.assertIn("'Widgte' not found", message)
        self.assertIn("did you mean: Widget (src/ui/widget.ts), WidgetFactory", message)

    def test_exit_3_without_suggestions_adds_nothing(self) -> None:
        # Most grep patterns are not symbol names; "not found" alone is noise.
        self.assertIsNone(self.search((3, NOT_FOUND_BARE), text=(0, {"results": []})))
        message = self.search((3, NOT_FOUND_BARE))
        self.assertNotIn("not found", message)
        self.assertIn("2 text hits", message)

    def test_pick_numbers_come_from_ix_never_the_list_position(self) -> None:
        message = self.search((2, dict(AMBIGUOUS, candidates=[
            {"name": "Widget", "kind": "class", "pick": 0},
            {"name": "Widget", "kind": "interface", "pick": 1},
        ])))
        self.assertIn("[0] Widget (class); [1] Widget (interface)", message)
        message = self.search((2, AMBIGUOUS_NO_PICK))
        self.assertIn("is ambiguous: Widget (class, src/ui/widget.ts:12); Widget (interface", message)
        self.assertNotIn("[1]", message)
        self.assertNotIn("--pick", message)
        self.assertIn("retry with --path <file>", message)

    def test_exit_4_adds_nothing(self) -> None:
        # Runs on every grep; mapping is the user's call, not the model's.
        self.assertIsNone(self.search((4, NOT_MAPPED), text=(0, {"results": []})))
        message = self.search((4, NOT_MAPPED))
        self.assertNotIn("mapped", message)
        self.assertNotIn("ix map", message)

    def test_an_older_ix_exiting_1_with_the_record(self) -> None:
        self.assertIn("[1] Widget (class, src/ui/widget.ts:12)", self.search((1, LOCATE_AMBIGUOUS)))

    def test_real_error_timeout_and_bad_json_behave_as_before(self) -> None:
        for label, locate in {
            "exit 1": (1, REAL_ERROR),
            "timeout": TimeoutError,
            "bad json": (2, "{not json"),
        }.items():
            with self.subTest(label):
                message = self.search(locate)
                self.assertEqual(
                    "[ix] bash grep intercepted for 'Widget' | 2 text hits in widget.ts, widget.py"
                    " | Prefer: ix text 'Widget' or ix locate 'Widget' over shell grep",
                    message,
                )
                self.assertIsNone(self.search(locate, text=(0, {"results": []})))

    def test_exit_0_is_unchanged(self) -> None:
        resolved = {"resolvedTarget": {"name": "Widget", "kind": "class", "path": "src/ui/widget.ts"}}
        self.assertIn("symbol: Widget (class, widget.ts)", self.search((0, resolved)))

    def test_long_candidate_lists_are_capped(self) -> None:
        many = dict(AMBIGUOUS, candidates=[{"name": f"W{i}", "kind": "class", "pick": i + 1} for i in range(12)])
        message = self.search((2, many))
        self.assertIn("[5] W4 (class)", message)
        self.assertNotIn("[6]", message)
        self.assertIn("+7 more", message)


class ReadPathTest(unittest.TestCase):
    def read(self, **answers):
        common = _load()
        fake = ScriptedIx(**answers)
        with patch.object(common.subprocess, "run", fake.run):
            return common.build_read_message("src/ui/widget.ts", None)

    def test_exit_2_adds_nothing(self) -> None:
        # The model named an exact file; the target ix found ambiguous is the
        # hook's own (the basename here), so "retry with --path" is noise.
        for code in (2, 1):  # 1: an older ix
            with self.subTest(code=code):
                self.assertIsNone(
                    self.read(inventory=(1, REAL_ERROR), overview=(code, AMBIGUOUS), impact=(code, AMBIGUOUS))
                )

    def test_a_duplicated_basename_is_not_called_ambiguous(self) -> None:
        common = _load()
        fake = ScriptedIx(
            inventory=(1, REAL_ERROR),
            overview=(2, AMBIGUOUS_NO_PICK),
            impact=(2, AMBIGUOUS_NO_PICK),
        )
        with patch.object(common.subprocess, "run", fake.run):
            message = common.build_read_message("src/a/utils.py", None)
        self.assertIn(["overview", "utils.py", "--format", "json"], fake.runs)
        self.assertIsNone(message)

    def test_exit_3_shows_the_suggestions(self) -> None:
        message = self.read(inventory=(1, REAL_ERROR), overview=(3, NOT_FOUND), impact=(3, NOT_FOUND))
        self.assertIn("not found", message)
        self.assertIn("did you mean: Widget (src/ui/widget.ts), WidgetFactory", message)

    def test_exit_4_adds_nothing(self) -> None:
        for code in (4, 1):  # 1: an older ix
            with self.subTest(code=code):
                self.assertIsNone(
                    self.read(inventory=(1, NOT_MAPPED), overview=(code, NOT_MAPPED), impact=(code, NOT_MAPPED))
                )

    def test_an_older_ix_exiting_1_with_the_record(self) -> None:
        message = self.read(inventory=(1, REAL_ERROR), overview=(1, NOT_FOUND), impact=(1, NOT_FOUND))
        self.assertIn("did you mean: Widget (src/ui/widget.ts), WidgetFactory", message)

    def test_real_error_timeout_and_bad_json_behave_as_before(self) -> None:
        self.assertIsNone(self.read(inventory=(1, REAL_ERROR), overview=(1, REAL_ERROR), impact=TimeoutError))
        self.assertIsNone(self.read(inventory=(1, REAL_ERROR), overview=(2, "nope"), impact=(3, "")))

    def test_results_still_win_alongside_a_lookup_answer(self) -> None:
        message = self.read(
            inventory=(0, {"summary": {"total": 2}, "results": [{"name": "Widget"}, {"name": "render"}]}),
            overview=(2, AMBIGUOUS),
            impact=(0, {"riskLevel": "high", "summary": {"directDependents": 7}}),
        )
        self.assertIn("2 entities: Widget, render", message)
        self.assertIn("HIGH RISK: 7 dependents", message)
        self.assertNotIn("ambiguous", message)

    def test_results_still_win_alongside_a_not_found(self) -> None:
        message = self.read(
            inventory=(0, {"summary": {"total": 1}, "results": [{"name": "Widget"}]}),
            overview=(3, NOT_FOUND),
            impact=(3, NOT_FOUND),
        )
        self.assertIn("1 entities: Widget", message)
        self.assertEqual(1, message.count("did you mean"), message)


class WritePathTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "src" / "ui").mkdir(parents=True)
        (self.root / "src" / "ui" / "widget.ts").write_text("export class Widget {}\n")

    def warn(self, impact, path="src/ui/widget.ts"):
        common = _load()
        with patch.object(common.subprocess, "run", ScriptedIx(impact=impact).run):
            return common.build_write_warning(path, self.root)

    def test_exit_2_lists_candidates(self) -> None:
        message = self.warn((2, AMBIGUOUS))
        self.assertTrue(message.startswith("[ix] widget.ts:"), message)
        self.assertIn("[1] Widget (class, src/ui/widget.ts:12)", message)
        self.assertIn("--pick <n>", message)

    def test_exit_3_shows_the_suggestions_and_stays_quiet_without(self) -> None:
        self.assertIn("did you mean: Widget (src/ui/widget.ts), WidgetFactory", self.warn((3, NOT_FOUND)))
        self.assertIn(
            "did you mean",
            self.warn((3, NOT_FOUND), path=str(self.root / "src" / "ui" / "widget.ts")),
        )
        self.assertIsNone(self.warn((3, NOT_FOUND_BARE)))

    def test_a_new_file_gets_no_suggestions(self) -> None:
        # A file the patch is creating is not in the graph yet; that is expected,
        # and a "did you mean" would point the model at some other file.
        self.assertIsNone(self.warn((3, NOT_FOUND), path="src/ui/new_widget.ts"))
        self.assertIsNone(self.warn((1, NOT_FOUND), path=str(self.root / "new_widget.ts")))

    def test_a_new_file_is_looked_for_where_the_command_runs(self) -> None:
        # The session runs in src/ui; "widget.ts" there exists, and a
        # same-named file at the workspace root must not make "new.ts" look old.
        (self.root / "new.ts").write_text("")
        common = _load()
        fake = ScriptedIx(impact=(3, NOT_FOUND))
        session = self.root / "src" / "ui"
        with patch.object(common.subprocess, "run", fake.run):
            self.assertIsNone(common.build_write_warnings(["new.ts"], self.root, session))
            self.assertIn("did you mean", common.build_write_warnings(["widget.ts"], self.root, session))
            # Without a session cwd, the workspace root is still the base.
            self.assertIn("did you mean", common.build_write_warning("new.ts", self.root))

    def test_exit_4_adds_nothing(self) -> None:
        common = _load()
        with patch.object(common.subprocess, "run", ScriptedIx(impact=(4, NOT_MAPPED)).run):
            self.assertIsNone(common.build_write_warnings(["a.py", "b.py", "c.py"], self.root))

    def test_an_older_ix_exiting_1_with_the_record(self) -> None:
        self.assertIn("[1] Widget", self.warn((1, AMBIGUOUS)))

    def test_real_error_timeout_and_bad_json_behave_as_before(self) -> None:
        for impact in ((1, REAL_ERROR), TimeoutError, (2, "garbage"), (0, "garbage")):
            with self.subTest(impact=impact):
                self.assertIsNone(self.warn(impact))

    def test_exit_0_is_unchanged(self) -> None:
        message = self.warn((0, {"riskLevel": "critical", "summary": {"directDependents": 9}}))
        self.assertEqual(
            "[ix] ⚠ CRITICAL EDIT — widget.ts has 9 dependents"
            " | Run: ix impact 'src/ui/widget.ts' for full blast radius",
            message,
        )


class UntouchedPathsTest(unittest.TestCase):
    def test_project_is_mapped_still_says_no_on_exit_4(self) -> None:
        common = _load()
        with patch.object(common.subprocess, "run", ScriptedIx(status=(4, NOT_MAPPED)).run):
            self.assertFalse(common.project_is_mapped(Path.cwd()))


if __name__ == "__main__":
    unittest.main()
