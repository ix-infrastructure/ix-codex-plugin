# Codex Hooks

This repo ports the `ix-claude-plugin` hook model into Codex's hook runtime.

## Event Mapping

| Codex Event | Script | Purpose |
|---|---|---|
| `SessionStart` | `hooks/session_start.py` | Inject the Ix operating model and graph-first rules |
| `UserPromptSubmit` | `hooks/user_prompt_submit.py` | Inject `ix briefing` once per 10 minutes when Ix Pro is available |
| `PreToolUse` (`Bash\|apply_patch`) | `hooks/pre_tool_use.py` | Pre-edit blast-radius warning + front-run shell search/read with Ix summaries |
| `PostToolUse` (`Bash\|apply_patch`) | `hooks/post_tool_use.py` | After a detected file write, request the guarded repository map (below) |
| `Stop` | `hooks/stop.py` | After each response, request the guarded repository map (below) |

The scripts live in the plugin, `plugins/ix-memory/hooks/`, beside the `hooks.json` Codex runs them from (below); a `--hooks` install copies them to `.codex/hooks/`. The five hooks share helpers in `ix_*.py`, one module per concern: `ix_hookio` (stdin/stdout), `ix_once` (one run per event when two copies are registered), `ix_proc` (running `ix`, Windows resolution and the cmd.exe shim guard), `ix_workspace` (git root, never `$HOME`), `ix_state` (caches), `ix_probes` (health and Pro), `ix_lookup`, `ix_shellparse`, `ix_patchparse`, `ix_summaries`, `ix_messages` and `ix_automap`. The hooks import them through `common.py`, which loads them from its own directory (symlinks resolved) and re-exports every name. A copy missing one of them makes every hook a silent no-op that prints a one-line hint on stderr. A `--hooks` install copies every `plugins/ix-memory/hooks/*.py`, so a normal install or upgrade picks them up.

## Host Protocol (Codex 0.155.1)

Verified against the `codex` 0.155.1 binary and `openai/codex@rust-v0.155.1` (`codex-rs/hooks/`):

- **Model context.** `hookSpecificOutput.additionalContext` (with `hookEventName` set to the event) is the only output Codex adds to the model's input on `SessionStart`, `UserPromptSubmit`, `PreToolUse` and `PostToolUse`. A top-level `systemMessage` becomes a UI warning only. `Stop` has no model channel; its output is `{"continue": true}`. The output structs deny unknown fields, so a misspelt key fails the hook run. `tests/test_codex_hook_protocol.py` validates every hook against the generated schemas, vendored in `tests/fixtures/codex-0.155.1/`.
- **Tool names.** Shell tools reach hooks as `Bash`. File edits use `apply_patch`, which Codex also matches as `Edit` and `Write`, with the raw patch as `tool_input.command`. Both pre and post tool hooks match `Bash|apply_patch`. The touched files are read from the patch's `*** Add File:` / `Update File:` / `Delete File:` / `Move to:` headers.
- **Shell.** Codex runs each `command` through the session shell without a login profile (`<shell> -c`). The inner `/bin/sh -c` stays non-login too. A login shell would source the user's profile on every hook, and anything that profile prints corrupts the hook's JSON.
- **Windows.** The session shell there is PowerShell (`shell-command/src/shell_detect.rs`, `default_user_shell`), so a hook runs as `powershell.exe -NoProfile -Command <commandWindows or command>` (`core/src/session/mod.rs` builds the hook shell with `derive_exec_args`). The installer gives each Ix hook a `commandWindows` of `& '<python>' '<...\.codex\hooks\_launch.py>' <hook>`: without `&`, a command that starts with a quoted path is a string expression to PowerShell, which is why the 2.4.x form `"python" "launcher" name` never ran. `command` keeps the POSIX form, so a repo-local `hooks.json` still works on macOS and Linux. Only when a session has no shell does Codex fall back to `%COMSPEC% /C`, where `&` is not valid.
- **Feature flag.** `hooks` has been stable and on by default since Codex 0.124 (openai/codex#19012). `codex_hooks` is a deprecated alias (`codex-rs/features/src/legacy.rs`), so the installer no longer writes it.
- **Trust.** User (`~/.codex/hooks.json`), project (`.codex/hooks.json`) and plugin-bundled hooks are all non-managed. Codex skips them until their exact definition is trusted, records trust as a `trusted_hash` of the normalised hook (event, matcher, command, timeout, statusMessage), and asks again whenever that hash changes (`codex-rs/hooks/src/engine/discovery.rs`). The TUI prompts "Hooks need review" at startup. `codex exec` cannot prompt, so it skips untrusted hooks unless it is run with `--dangerously-bypass-hook-trust`. Changing `hooks.json`, as this release does, requires the user to review the hooks again.

## Plugin-Bundled Hooks

`plugins/ix-memory/hooks/hooks.json` registers the five hooks. Codex loads `hooks/hooks.json` from an installed plugin by default, so `plugin.json` names no `hooks` entry. Checked against Codex 0.162.1 with the plugin installed from a local marketplace and no `hooks.json` of the user's: the app server's `hooks/list` reports all five with `"source": "plugin"`, and a turn with one shell call runs `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse` and `Stop` from `$CODEX_HOME/plugins/cache/<marketplace>/ix-memory/<version>/hooks/`, with `PLUGIN_ROOT` (and `CLAUDE_PLUGIN_ROOT`) set to that directory and `PLUGIN_DATA` to `plugins/data/ix-memory-<marketplace>`. The 0.155.1 source review behind the Host Protocol notes above found plugin hooks loaded there already (`plugin_hooks` is a removed flag, so they are always on), so the installer takes 0.155 as its minimum.

- **Commands.** `command` is `python3 "$PLUGIN_ROOT/hooks/<name>.py"`, run through the session shell. `commandWindows` is for PowerShell: `py -3` when the launcher is installed, otherwise `python`, on `"$env:PLUGIN_ROOT\hooks\<name>.py"`, the same interpreter order as `codex-install.ps1`. The Windows form has not been run under Codex here.
- **Trust.** Plugin hooks need review and trust like any other non-managed hook. Enabling the plugin does not trust them, and an update that changes their definitions asks again.
- **Updates.** Codex copies the plugin into its cache when it is installed, and refreshes that copy only when the plugin is installed again, not when the installer rewrites the marketplace source. The cached copy is what runs.
- **`--hooks`.** Kept for a Codex that does not run plugin hooks. A `--home` install that finds Codex 0.155 or later on PATH, `[plugins."ix-memory@<marketplace>"] enabled = true` in `config.toml` and `hooks/hooks.json` in Codex's cached copy removes its own entries from `~/.codex/hooks.json` (the file too, if nothing else is left in it) and its scripts from `~/.codex/hooks/`, and keeps every other hook. If any of that cannot be read, it installs the `hooks.json` entries as before. A `--repo` install never does this: a project's `.codex/hooks.json` is shared with teammates whose Codex this machine cannot see.
- **Both registered.** Between a plugin update in Codex and the next installer run, or with a project `.codex/hooks.json` as well, Codex runs every copy. It starts them together and hands each the same bytes on stdin (checked against 0.162.1), so `ix_once` lets the first copy to claim those bytes (an exclusive file under the state directory, keyed by their hash) run and the others exit 0 with no output. Input without `session_id` and `hook_event_name` is never dropped, and any error claiming the event lets the copy run.
- **Still to do.** `ix hook codex-<event>` entry points in the Ix CLI would let the hooks run without Python. That is a change in the CLI first.

## MCP Tools

The MCP tools come from the Ix CLI's own server, `ix mcp`. The plugin ships no MCP server code and installs no MCP file.

`ix mcp install --host codex` registers `ix mcp` with Codex as the `ix-memory` server (`codex mcp add` writes it to `~/.codex/config.toml`, or `$CODEX_HOME/config.toml` when that is set).

The installer's `--mcp` step runs that command with a time limit and reports the outcome in one line: registered, already registered, a different `ix-memory` server left alone (`--force` replaces it), or no `codex` on PATH.

Versions up to 2.4.1 copied a Python server to `.codex/mcp/server.py`. An upgrade replaces that registration with `ix mcp` and removes the old files.

## Agent Playbooks

The five playbooks in `agents/` are reference text, kept for parity with `ix-claude-plugin`. No installer mode copies them, and the plugin manifest (`plugins/ix-memory/.codex-plugin/plugin.json`) does not declare them, so installing the plugin does not give Codex these as agents. Use them as prompts to paste or adapt. The skills (`$ix-*`) are the supported workflows.

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

**When a lookup has no results:** `ix locate`, `ix overview` and `ix impact` exit 2 (ambiguous target), 3 (not found) or 4 (workspace not mapped) with a JSON record on stdout; older ix exits 1 with the same record. The hook passes that answer on in one line instead of dropping it: the first 5 candidates and a hint to retry with `--path` (with `--pick <n>` too when ix numbers the candidates itself; the hook never numbers them), or the suggestions for a target it could not find. A not-found with no suggestions adds nothing, and so does a not-found for a file that a write is creating (looked for relative to the directory the command runs in). The read hook leaves ambiguity out: the model named an exact file, and the read hook often asks ix about just the basename, so a same-named file elsewhere is not news. A workspace that is not mapped adds nothing: these hooks run on every grep, read and write, and mapping is the user's call. Any other failure stays silent, as before.

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
| First-class agent delegation | Yes | Not installed | `agents/*.md` are reference text; the `$ix-*` skills cover the workflows |
| MCP tools | Via hooks settings | From `ix mcp` | Registered by the installer's MCP step |

## Safety Model

All hook scripts are intentionally no-op friendly:
- If `ix` is missing or unhealthy, or the session is not in a project (e.g. `$HOME`), every hook exits silently
- If write detection produces no useful data, no output is emitted
- Hooks add context; they never block the underlying Codex tool call
- The automatic map is fire-and-forget — failures are silent

## write Detection Coverage

The `detect_file_write()` function in `ix_shellparse.py` (re-exported by `common.py`) handles:
- `> file` and `>> file` output redirections (excluding `2>` stderr redirects)
- `tee file` invocations
- Editor commands: `vim`, `vi`, `nvim`, `nano`, `emacs`, `hx`, `micro`
- Heredoc patterns: `cat << 'EOF' > file`

It does NOT attempt to detect:
- Multi-command pipelines with shell operators (conservative — avoids false positives)
- Dynamic paths like `> "$VAR"` (variable not expanded at hook time)
- Python/Node scripts that write files internally
