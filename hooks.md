# Codex Hooks

This repo ports the `ix-claude-plugin` hook model into Codex's hook runtime.

## Event Mapping

| Codex Event | Script | Purpose |
|---|---|---|
| `SessionStart` | `.codex/hooks/session_start.py` | Inject the Ix operating model and graph-first rules |
| `UserPromptSubmit` | `.codex/hooks/user_prompt_submit.py` | Inject `ix briefing` once per 10 minutes when Ix Pro is available |
| `PreToolUse` (`Bash`) | `.codex/hooks/pre_tool_use.py` | Pre-edit blast-radius warning + front-run shell search/read with Ix summaries |
| `PostToolUse` (`Bash`) | `.codex/hooks/post_tool_use.py` | Trigger per-file `ix map` after detected file writes |
| `Stop` | `.codex/hooks/stop.py` | Run full `ix map` asynchronously after each response |

## MCP Availability (Verified)

`codex mcp` subcommand is present in Codex CLI 0.125.0 — MCP server management is available.  
MCP server implementation for this plugin is deferred (Phase 3 task) pending design of the Python MCP server.

## Agent Delegation

Codex agent delegation status is **not yet verified** as first-class runtime. The five agent playbooks
in `agents/` remain documentation-only until confirmed.

## What The PreToolUse Hook Does

**Write detection (pre-edit gate):**
- Parses the Bash command string for output redirections (`>`, `>>`), `tee` invocations, and editor commands
- When a write target is detected, runs `ix impact <file>` before the command executes
- Injects a one-line blast-radius warning if risk is medium/high/critical with 3+ dependents
- Never blocks the command — always advisory

**Search interception:**
- For `grep` and `rg` commands: extracts the search pattern, runs `ix text` + `ix locate`, injects a one-line graph-aware summary

**Read interception:**
- For read-style commands (`cat`, `sed`, `head`, `tail`, `awk`): extracts the target file path, runs `ix inventory` + `ix overview` + `ix impact`, injects a one-line summary

**Priority order:** write detection → search interception → read interception (first match wins).

## What The PostToolUse Hook Does

- Reads the same Bash command from the PostToolUse event
- Detects file write operations using `detect_file_write()` (same logic as PreToolUse)
- For each written file (up to 3): fires `ix map <path>` asynchronously to keep the graph current mid-session
- Complements `stop.py` — per-file ingest runs immediately after the write, full-map refresh runs at stop

## Known Limitations vs Claude

| Capability | Claude | Codex | Notes |
|---|---|---|---|
| Edit-specific PreToolUse matcher | `Edit`, `Write`, `MultiEdit` events | Bash command parse only | Codex only exposes generic Bash tool; write detection via regex |
| Grep/Glob tool interception | Dedicated `Grep`/`Glob` matchers | Bash command parse only | Same limitation as write detection |
| `file_path` in PreToolUse event | Direct from `tool_input.file_path` | Parsed from Bash command string | Less reliable for complex pipelines |
| First-class agent delegation | Yes | Unknown — docs-only | Pending Codex agent runtime verification |
| MCP tools | Via hooks settings | Available (`codex mcp`) | Python MCP server not yet implemented |

## Safety Model

All hook scripts are intentionally no-op friendly:
- If `ix` is missing or unhealthy, every hook exits silently
- If write detection produces no useful data, no output is emitted
- Hooks add context; they never block the underlying Codex tool call
- Background ingest jobs (`spawn_background_ix_ingest`) are fire-and-forget — failures are silent

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
