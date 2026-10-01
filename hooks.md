# Codex Hooks

This repo ports the `ix-claude-plugin` hook model into Codex's hook runtime.

## Event Mapping

| Codex Event | Script | Purpose |
|---|---|---|
| `SessionStart` | `.codex/hooks/session_start.py` | Inject the Ix operating model and graph-first rules |
| `UserPromptSubmit` | `.codex/hooks/user_prompt_submit.py` | Inject `ix briefing` once per 10 minutes when Ix Pro is available |
| `PreToolUse` (`Bash\|apply_patch`) | `.codex/hooks/pre_tool_use.py` | Pre-edit blast-radius warning + front-run shell search/read with Ix summaries |
| `PostToolUse` (`Bash\|apply_patch`) | `.codex/hooks/post_tool_use.py` | After a detected file write, request the guarded repository map (below) |
| `Stop` | `.codex/hooks/stop.py` | After each response, request the guarded repository map (below) |

## Host Protocol (Codex 0.155.1)

Verified against the `codex` 0.155.1 binary and `openai/codex@rust-v0.155.1` (`codex-rs/hooks/`):

- **Model context.** `hookSpecificOutput.additionalContext` (with `hookEventName` set to the event) is the only output Codex adds to the model's input on `SessionStart`, `UserPromptSubmit`, `PreToolUse` and `PostToolUse`. A top-level `systemMessage` becomes a UI warning only. `Stop` has no model channel; its output is `{"continue": true}`. The output structs deny unknown fields, so a misspelt key fails the hook run. `tests/test_codex_hook_protocol.py` validates every hook against the generated schemas, vendored in `tests/fixtures/codex-0.155.1/`.
- **Tool names.** Shell tools reach hooks as `Bash`. File edits use `apply_patch`, which Codex also matches as `Edit` and `Write`, with the raw patch as `tool_input.command`. Both pre and post tool hooks match `Bash|apply_patch`. The touched files are read from the patch's `*** Add File:` / `Update File:` / `Delete File:` / `Move to:` headers.
- **Shell.** Codex runs each `command` through the session shell without a login profile (`<shell> -c`). The inner `/bin/sh -c` stays non-login too. A login shell would source the user's profile on every hook, and anything that profile prints corrupts the hook's JSON.
- **Feature flag.** `hooks` has been stable and on by default since Codex 0.124 (openai/codex#19012). `codex_hooks` is a deprecated alias (`codex-rs/features/src/legacy.rs`), so the installer no longer writes it.
- **Trust.** User (`~/.codex/hooks.json`), project (`.codex/hooks.json`) and plugin-bundled hooks are all non-managed. Codex skips them until their exact definition is trusted, records trust as a `trusted_hash` of the normalised hook (event, matcher, command, timeout, statusMessage), and asks again whenever that hash changes (`codex-rs/hooks/src/engine/discovery.rs`). The TUI prompts "Hooks need review" at startup. `codex exec` cannot prompt, so it skips untrusted hooks unless it is run with `--dangerously-bypass-hook-trust`. Changing `hooks.json`, as this release does, requires the user to review the hooks again.

## Moving to Plugin-Bundled Hooks (not done)

Codex 0.155 loads hooks bundled in an installed plugin: `hooks/hooks.json` under the plugin root by default, or a `hooks` entry in `.codex-plugin/plugin.json` (a path, paths, or inline objects). `plugin_hooks` is listed as a removed flag, so plugin hooks are always on. They still need trust exactly like `~/.codex/hooks.json`, and installing or enabling the plugin does not trust them. The move would not remove the review step. What it would change:

1. Add `plugins/ix-memory/hooks/hooks.json` with the five events. Commands would use `$PLUGIN_ROOT` (Codex sets `PLUGIN_ROOT`/`PLUGIN_DATA`, plus the `CLAUDE_PLUGIN_*` aliases), e.g. `python3 "$PLUGIN_ROOT/hooks/session_start.py"`. That replaces the `$PWD`-walking one-liner and the project-over-home precedence.
2. Move `.codex/hooks/*.py` into `plugins/ix-memory/hooks/`, and the caches into `$PLUGIN_DATA` if wanted.
3. Windows: the installer's per-machine rewrite to an absolute interpreter path cannot apply to a file shipped inside the plugin. Use `commandWindows` (accepted by `HookHandlerConfig`) with a command the Windows session shell can parse. Codex hands it to PowerShell as `-NoProfile -Command <cmd>`, or to `cmd /c` (`codex-rs/core/src/shell.rs`, `derive_exec_args`).
4. Retire `--hooks`, or keep it only for Codex versions that predate plugin hooks, and have it stop writing `~/.codex/hooks.json` so the two sources do not both fire.
5. Hook enablement then follows the plugin's enable and disable lifecycle, and a plugin update that changes the hook definitions prompts for review again.

## MCP Availability (Verified)

`codex mcp` subcommand is present in Codex CLI 0.125.0 — MCP server management is available.  
MCP server implementation for this plugin is deferred (Phase 3 task) pending design of the Python MCP server.

## Agent Delegation

Codex agent delegation status is **not yet verified** as first-class runtime. The five agent playbooks
in `agents/` remain documentation-only until confirmed.

## What The PreToolUse Hook Does

**Write detection (pre-edit gate):**
- For `apply_patch`: every file the patch adds, updates, deletes or moves to
- For `Bash`: output redirections (`>`, `>>`), `tee` invocations, and editor commands
- When a write target is detected, runs `ix impact <file>` before the tool executes (the first 3 files, in parallel)
- Injects a one-line blast-radius warning if risk is medium/high/critical with 3+ dependents
- Never blocks the command — always advisory

**Search interception:**
- For `grep` and `rg` commands: extracts the search pattern, runs `ix text` + `ix locate`, injects a one-line graph-aware summary

**Read interception:**
- For read-style commands (`cat`, `sed`, `head`, `tail`, `awk`): extracts the target file path, runs `ix inventory` + `ix overview` + `ix impact`, injects a one-line summary

**Priority order:** write detection → search interception → read interception (first match wins).

## What The PostToolUse Hook Does

- Reads the same tool input from the PostToolUse event
- Detects file writes with `files_written()` (same logic as PreToolUse: patch headers for `apply_patch`, `detect_file_write()` for `Bash`)
- On a write, requests the same guarded repository map as `stop.py` — never `ix map <file>`, which the CLI rejects ("Map path is not a directory")

## The Guarded Automatic Map

`PostToolUse` and `Stop` refresh the graph only when every guard holds:
- the hook payload's `cwd` is inside a git repository (`git rev-parse --show-toplevel`), and that root is not `$HOME`
- the repository is already mapped: `ix status --format json --root <root>` reports `graphCompleted: true` (a hook never creates a workspace)
- no automatic map for that root in the last 2 minutes (debounce stamps live in the per-user state directory, keyed by root)

The map is then `ix map <root> --silent`, run from the root with `IX_AUTO_MAP=1`, detached so the hook returns immediately.

Hook caches and debounce stamps live in `${XDG_STATE_HOME:-~/.local/state}/ix-codex-plugin/` (`%LOCALAPPDATA%\ix-codex-plugin\` on Windows), not a shared `/tmp` path.

## Known Limitations vs Claude

| Capability | Claude | Codex | Notes |
|---|---|---|---|
| Edit-specific PreToolUse matcher | `Edit`, `Write`, `MultiEdit` events | `apply_patch` (+ Bash redirect parse) | Codex edits files with `apply_patch`; files come from the patch headers |
| Grep/Glob tool interception | Dedicated `Grep`/`Glob` matchers | Bash command parse only | Same limitation as write detection |
| `file_path` in PreToolUse event | Direct from `tool_input.file_path` | Parsed from the patch, or from the Bash command string | Bash parsing is less reliable for complex pipelines |
| First-class agent delegation | Yes | Unknown — docs-only | Pending Codex agent runtime verification |
| MCP tools | Via hooks settings | Available (`codex mcp`) | Python MCP server not yet implemented |

## Safety Model

All hook scripts are intentionally no-op friendly:
- If `ix` is missing or unhealthy, or the session is not in a project (e.g. `$HOME`), every hook exits silently
- If write detection produces no useful data, no output is emitted
- Hooks add context; they never block the underlying Codex tool call
- The automatic map is fire-and-forget — failures are silent

## write Detection Coverage

The `detect_file_write()` function in `common.py` handles:
- `> file` and `>> file` output redirections (excluding `2>` stderr redirects)
- `tee file` invocations
- Editor commands: `vim`, `vi`, `nvim`, `nano`, `emacs`, `hx`, `micro`
- Heredoc patterns: `cat << 'EOF' > file`

It does NOT attempt to detect:
- Multi-command pipelines with shell operators (conservative — avoids false positives)
- Dynamic paths like `> "$VAR"` (variable not expanded at hook time)
- Python/Node scripts that write files internally
