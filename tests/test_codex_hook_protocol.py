#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""What the hooks print, held to the protocol Codex actually speaks.

The PreToolUse hook printed `{"systemMessage": ...}`. That is valid output --
and it never reached the model. Codex turns a `systemMessage` into a warning in
the user's transcript; only `hookSpecificOutput.additionalContext` is added to
the model's input (codex-rs/hooks/src/events/pre_tool_use.rs, `parse_completed`,
at rust-v0.155.1). The suites asserted the shape the hook happened to print, so
they could not tell.

Two things are checked for every hook here:

* its stdout validates against the output schema Codex generates for that event.
  The schemas in fixtures/codex-0.155.1/ are copied verbatim from
  openai/codex@rust-v0.155.1 (codex-rs/hooks/schema/generated/, Apache-2.0).
  Codex deserialises them with `deny_unknown_fields`, so an extra or misspelt
  key fails the hook run rather than being ignored;
* the text meant for the model is where Codex reads model context from --
  `model_context_of` below follows Codex's own parse, not the hook's.

And the hooks.json registration: `apply_patch` is how Codex edits files, so a
`Bash`-only matcher never saw an edit.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO / ".codex" / "hooks"
SCHEMAS = Path(__file__).resolve().parent / "fixtures" / "codex-0.155.1"

SCHEMA_FILES = {
    "SessionStart": "session-start.command.output.schema.json",
    "UserPromptSubmit": "user-prompt-submit.command.output.schema.json",
    "PreToolUse": "pre-tool-use.command.output.schema.json",
    "PostToolUse": "post-tool-use.command.output.schema.json",
    "Stop": "stop.command.output.schema.json",
}

# Events whose output has a model channel at all. Stop has none: its only lever
# on the model is `decision: block`, which forces another turn.
MODEL_CONTEXT_EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse"}
# Codex also takes plain, non-JSON stdout as model context for these two
# (events/session_start.rs and events/user_prompt_submit.rs); for the tool events
# plain stdout is dropped.
PLAIN_STDOUT_IS_CONTEXT = {"SessionStart", "UserPromptSubmit"}

PATCH = (
    "*** Begin Patch\n"
    "*** Update File: src/core.py\n"
    "@@ def run():\n"
    "-    return 1\n"
    "+    return 2\n"
    "*** Add File: src/new_module.py\n"
    "+print('hi')\n"
    "*** Delete File: old/legacy.py\n"
    "*** Update File: src/a.py\n"
    "*** Move to: src/b.py\n"
    "@@\n"
    "-x\n"
    "+y\n"
    "*** End Patch\n"
)


# ── A draft-07 subset: exactly what Codex's generated hook schemas use ───────


def _resolve(schema: dict, root: dict) -> dict:
    ref = schema.get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        return _resolve(root["definitions"][name], root)
    return schema


def validate(instance: object, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    root = root if root is not None else schema
    schema = _resolve(schema, root)
    errors: list[str] = []
    for sub in schema.get("allOf", []):
        errors += validate(instance, sub, root, path)
    expected = schema.get("type")
    kinds = {"object": dict, "string": str, "boolean": bool}
    if expected in kinds and not isinstance(instance, kinds[expected]):
        return errors + [f"{path}: expected {expected}, got {type(instance).__name__}"]
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in {schema['enum']!r}")
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required {key!r}")
        for key, value in instance.items():
            if key in properties:
                errors += validate(value, properties[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unknown field {key!r}")
    return errors


def load_schema(event: str) -> dict:
    return json.loads((SCHEMAS / SCHEMA_FILES[event]).read_text(encoding="utf-8"))


def model_context_of(event: str, stdout: str) -> str | None:
    """The text Codex 0.155.1 adds to the model's input for this hook output."""
    text = stdout.strip()
    if not text or event not in MODEL_CONTEXT_EVENTS:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text if event in PLAIN_STDOUT_IS_CONTEXT else None
    if not isinstance(payload, dict) or validate(payload, load_schema(event)):
        return None  # Codex marks the run failed and uses none of it
    specific = payload.get("hookSpecificOutput") or {}
    return specific.get("additionalContext")


class SchemaHarnessTest(unittest.TestCase):
    """The harness itself must reject what Codex rejects."""

    def test_unknown_and_misplaced_fields_are_rejected(self) -> None:
        schema = load_schema("PreToolUse")
        self.assertEqual([], validate({"hookSpecificOutput": {"hookEventName": "PreToolUse"}}, schema))
        self.assertTrue(validate({"additionalContext": "x"}, schema))
        self.assertTrue(validate({"hookSpecificOutput": {"hookEventName": "PostToolUse"}}, schema))
        self.assertTrue(validate({"hookSpecificOutput": {"additionalContext": "x"}}, schema))

    def test_a_system_message_never_reaches_the_model(self) -> None:
        """The old PreToolUse shape: schema-valid, and invisible to the model."""
        old = json.dumps({"systemMessage": "[ix] ⚠ HIGH-RISK EDIT"})
        self.assertEqual([], validate(json.loads(old), load_schema("PreToolUse")))
        self.assertIsNone(model_context_of("PreToolUse", old))


# ── The hooks themselves, end to end in-process ─────────────────────────────


def _load(name: str, cache_dir: Path):
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    for stale in ("common", name):
        sys.modules.pop(stale, None)
    spec = importlib.util.spec_from_file_location("common", HOOKS_DIR / "common.py")
    common = importlib.util.module_from_spec(spec)
    sys.modules["common"] = common
    spec.loader.exec_module(common)
    common.CACHE_DIR = cache_dir
    common.STATUS_CACHE_PATH = cache_dir / "ix-status.json"
    common.PRO_CACHE_PATH = cache_dir / "ix-pro.json"
    common.BRIEFING_CACHE_PATH = cache_dir / "ix-briefing.txt"
    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return common, module


class StrictIx:
    """Answers the calls these hooks make, and fails anything the CLI rejects."""

    UNKNOWN_OPTIONS = {"locate": {"--limit"}}
    COMMANDS = {"status", "map", "text", "locate", "inventory", "overview", "impact", "briefing"}
    HIGH_RISK = {"riskLevel": "high", "summary": {"directDependents": 7}}

    def __init__(self, real_run) -> None:
        self.real_run = real_run
        self.calls: list[list[str]] = []
        self.errors: list[str] = []

    def __call__(self, argv, **kwargs):
        if argv[0] == "git":
            return self.real_run(argv, **kwargs)
        args = list(argv[1:])
        self.calls.append(args)
        command = args[0] if args else ""
        if command not in self.COMMANDS:
            self.errors.append(f"unknown command {command!r}")
            return subprocess.CompletedProcess(argv, 1, "", "unknown command")
        for option in self.UNKNOWN_OPTIONS.get(command, ()):
            if option in args:
                self.errors.append(f"unknown option {option} for {command}")
                return subprocess.CompletedProcess(argv, 1, "", "unknown option")
        out: object = {}
        if command == "status":
            out = {"backend": "ok", "graphCompleted": False}
        elif command == "impact":
            out = self.HIGH_RISK
        elif command == "text":
            out = {"results": [{"path": "src/core.py"}]}
        elif command == "locate":
            out = {"resolvedTarget": {"name": "run", "kind": "function", "path": "src/core.py"}}
        elif command == "briefing":
            out = {"plans": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")


class HookOutputProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.cwd = self.tmp / "project"
        self.cwd.mkdir()

    def run_hook(self, name: str, event: dict) -> tuple[str, StrictIx]:
        common, hook = _load(name, self.tmp / "state")
        ix = StrictIx(common.run_command)
        stdout = io.StringIO()
        with patch.object(common, "ix_available", return_value=True), patch.object(
            common, "run_command", side_effect=ix
        ), patch.object(common, "spawn_background_ix_map", return_value=True), patch.object(
            sys, "stdin", io.StringIO(json.dumps({"cwd": str(self.cwd), **event}))
        ), contextlib.redirect_stdout(stdout):
            hook.main()
        self.assertEqual([], ix.errors, "a hook made a call the real CLI rejects")
        return stdout.getvalue(), ix

    def assert_valid(self, event: str, stdout: str) -> dict:
        self.assertTrue(stdout.strip(), f"{event} hook printed nothing")
        payload = json.loads(stdout)
        self.assertEqual([], validate(payload, load_schema(event)))
        return payload

    def test_session_start_guidance_reaches_the_model(self) -> None:
        stdout, _ = self.run_hook("session_start", {"hook_event_name": "SessionStart", "source": "startup"})
        self.assert_valid("SessionStart", stdout)
        self.assertIn("Ix Memory is available", model_context_of("SessionStart", stdout) or "")

    def test_user_prompt_briefing_reaches_the_model(self) -> None:
        stdout, _ = self.run_hook("user_prompt_submit", {"hook_event_name": "UserPromptSubmit", "prompt": "hi"})
        self.assert_valid("UserPromptSubmit", stdout)
        self.assertIn("[ix] Session briefing", model_context_of("UserPromptSubmit", stdout) or "")

    def test_bash_search_interception_reaches_the_model(self) -> None:
        """Fails on the old `{"systemMessage": ...}` output."""
        stdout, _ = self.run_hook(
            "pre_tool_use",
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "rg run src"}},
        )
        payload = self.assert_valid("PreToolUse", stdout)
        self.assertNotIn("systemMessage", payload)
        context = model_context_of("PreToolUse", stdout)
        self.assertIsNotNone(context, "the interception never reaches the model")
        self.assertIn("[ix] bash grep intercepted for 'run'", context)

    def test_bash_write_warning_reaches_the_model(self) -> None:
        stdout, ix = self.run_hook(
            "pre_tool_use",
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "echo x > src/core.py"}},
        )
        self.assert_valid("PreToolUse", stdout)
        self.assertIn("HIGH-RISK EDIT — core.py", model_context_of("PreToolUse", stdout) or "")
        self.assertIn(["impact", "src/core.py", "--format", "json"], ix.calls)

    def test_apply_patch_edit_warning_reaches_the_model(self) -> None:
        stdout, ix = self.run_hook(
            "pre_tool_use",
            {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"command": PATCH}},
        )
        self.assert_valid("PreToolUse", stdout)
        context = model_context_of("PreToolUse", stdout) or ""
        self.assertIn("HIGH-RISK EDIT — core.py", context)
        self.assertIn("HIGH-RISK EDIT — new_module.py", context)
        # The first MAX_WRITE_WARNINGS files, checked in parallel (any order).
        impacts = sorted(c[1] for c in ix.calls if c[0] == "impact")
        self.assertEqual(["old/legacy.py", "src/core.py", "src/new_module.py"], impacts)
        # A patch is never treated as a shell command to intercept.
        self.assertFalse([c for c in ix.calls if c[0] in {"text", "locate", "overview"}])

    def test_post_tool_use_prints_nothing_or_valid_output(self) -> None:
        for tool_name, command in (("Bash", "echo x > a.py"), ("apply_patch", PATCH)):
            stdout, _ = self.run_hook(
                "post_tool_use",
                {"hook_event_name": "PostToolUse", "tool_name": tool_name, "tool_input": {"command": command}},
            )
            if stdout.strip():
                self.assert_valid("PostToolUse", stdout)

    def test_stop_output_is_valid(self) -> None:
        stdout, _ = self.run_hook("stop", {"hook_event_name": "Stop"})
        self.assert_valid("Stop", stdout)


class ApplyPatchParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.common, _ = _load("pre_tool_use", Path(tempfile.gettempdir()))

    def test_every_hunk_header_is_a_touched_file(self) -> None:
        self.assertEqual(
            ["src/core.py", "src/new_module.py", "old/legacy.py", "src/a.py", "src/b.py"],
            self.common.parse_apply_patch_paths(PATCH),
        )

    def test_added_lines_that_look_like_headers_are_not_files(self) -> None:
        patch_text = "*** Begin Patch\n*** Add File: doc.md\n+*** Update File: fake.py\n*** End Patch\n"
        self.assertEqual(["doc.md"], self.common.parse_apply_patch_paths(patch_text))

    def test_codex_trims_marker_lines_and_so_does_this(self) -> None:
        patch_text = "*** Begin Patch\n  *** Update File: spaced.py  \r\n@@\n-a\n+b\n*** End Patch"
        self.assertEqual(["spaced.py"], self.common.parse_apply_patch_paths(patch_text))

    def test_files_written_dispatches_on_tool_name(self) -> None:
        files_written = self.common.files_written
        self.assertEqual(["x.py"], files_written({"tool_name": "Bash", "tool_input": {"command": "echo 1 > x.py"}}))
        self.assertEqual(
            ["src/core.py"],
            files_written({"tool_name": "apply_patch", "tool_input": {"command": PATCH}})[:1],
        )
        # A redirect inside patch text is content, not a shell write.
        self.assertEqual([], files_written({"tool_name": "apply_patch", "tool_input": {"command": "echo 1 > x.py"}}))
        self.assertEqual([], files_written({"tool_name": "Bash", "tool_input": "not an object"}))


# ── hooks.json, as Codex reads it ────────────────────────────────────────────


def codex_matches(matcher: str | None, tool_name: str, aliases: tuple[str, ...] = ()) -> bool:
    """codex-rs/hooks/src/events/common.rs `matches_matcher`, for the exact form.

    `apply_patch` is matched by its canonical name and by its `Edit`/`Write`
    aliases (codex-rs/core/src/tools/hook_names.rs).
    """
    if matcher in (None, "", "*"):
        return True
    assert all(ch.isalnum() or ch in "_|" for ch in matcher), "regex matchers not modelled"
    return any(candidate in (tool_name, *aliases) for candidate in matcher.split("|"))


class HooksJsonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hooks = json.loads((REPO / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]

    def _matchers(self, event: str) -> list[str | None]:
        return [group.get("matcher") for group in self.hooks[event]]

    def test_edits_reach_the_pre_and_post_tool_hooks(self) -> None:
        for event in ("PreToolUse", "PostToolUse"):
            matchers = self._matchers(event)
            self.assertTrue(any(codex_matches(m, "apply_patch", ("Write", "Edit")) for m in matchers), event)
            self.assertTrue(any(codex_matches(m, "Bash") for m in matchers), event)

    def test_no_login_shell(self) -> None:
        """A login shell sources the user's profile, and any output it prints
        lands in front of the hook's JSON on stdout."""
        for event, groups in self.hooks.items():
            for group in groups:
                for handler in group["hooks"]:
                    command = handler["command"]
                    self.assertNotIn(" -lc ", command, event)
                    self.assertNotIn(" -l ", command, event)
                    self.assertTrue(command.startswith("/bin/sh -c '"), event)

    def test_every_hook_event_is_one_codex_knows(self) -> None:
        self.assertEqual(set(SCHEMA_FILES), set(self.hooks))


@unittest.skipUnless(os.name == "posix", "runs hooks.json's /bin/sh command")
class HooksJsonCommandTest(unittest.TestCase):
    """The registered command line, run the way Codex runs it, finds the hook."""

    def test_command_runs_the_nearest_hook_without_a_login_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            # A profile that prints: under `sh -l` this would corrupt stdout.
            (home / ".profile").write_text("echo PROFILE-NOISE\n")
            project = Path(tmp) / "project"
            hooks_dir = project / ".codex" / "hooks"
            hooks_dir.mkdir(parents=True)
            (hooks_dir / "stop.py").write_text("print('{\"continue\": true}')\n")
            hooks = json.loads((REPO / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
            command = hooks["Stop"][0]["hooks"][0]["command"]
            # Codex runs `<user shell> -c <command>` (session/mod.rs,
            # build_hooks_config: derive_exec_args(.., use_login_shell=false)).
            result = subprocess.run(
                ["/bin/sh", "-c", command],
                cwd=project,
                env={"HOME": str(home), "PATH": os.environ.get("PATH", ""), "PWD": str(project)},
                input="{}",
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual({"continue": True}, json.loads(result.stdout))


class InstallerFeatureFlagTest(unittest.TestCase):
    """`codex_hooks` is a legacy alias of the stable, default-on `hooks`."""

    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "install_codex_integration", REPO / "scripts" / "install_codex_integration.py"
        )
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)

    def test_install_hooks_writes_no_feature_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            installed = self.installer.install_hooks(target, "copy", force=False)
            self.assertFalse((target / ".codex" / "config.toml").exists())
            self.assertNotIn(target / ".codex" / "config.toml", installed)

    def test_an_existing_config_is_left_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            config = target / ".codex" / "config.toml"
            config.parent.mkdir()
            original = b'model = "o3"\n\n[projects."/x"]\ntrust_level = "trusted"\n'
            config.write_bytes(original)
            self.installer.install_hooks(target, "copy", force=False)
            self.assertEqual(original, config.read_bytes())

    @unittest.skipIf(sys.version_info < (3, 11), "reads TOML with tomllib")
    def test_a_config_that_turns_hooks_off_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            for text, expected in (
                ("[features]\nhooks = false\n", "hooks"),
                ("[features]\ncodex_hooks = false\n", "codex_hooks"),
                ("features.hooks = false\n", "hooks"),
                ("[features]\nhooks = true\n", None),
                ("model = 'o3'\n", None),
                ("not toml [", None),
            ):
                config.write_text(text, encoding="utf-8")
                self.assertEqual(expected, self.installer.hooks_disabled_by(config), text)
            self.assertIsNone(self.installer.hooks_disabled_by(Path(tmp) / "missing.toml"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
