# Codex Hooks

This repo ports the `ix-claude-plugin` hook model into Codex's hook runtime.

## Event Mapping

| Codex Event | Script | Purpose |
|---|---|---|
| `SessionStart` | `.codex/hooks/session_start.py` | Inject the Ix operating model and graph-first rules |
| `UserPromptSubmit` | `.codex/hooks/user_prompt_submit.py` | Inject `ix briefing` once per 10 minutes when Ix Pro is available |
| `PreToolUse` (`Bash`) | `.codex/hooks/pre_tool_use.py` | Front-run shell search/read commands with Ix summaries |
| `Stop` | `.codex/hooks/stop.py` | Run `ix map` asynchronously after each response |

## What The Bash Hook Does

For `grep` and `rg` commands:
- extracts the search pattern
- runs `ix text` and, for plain identifiers, `ix locate`
- injects a one-line graph-aware summary before the shell command runs

For read-style shell commands such as `cat`, `sed`, `head`, `tail`, and `awk`:
- extracts the target file path when the command is simple enough to parse safely
- runs `ix inventory`, `ix overview`, and `ix impact` on the target filename
- injects a one-line summary nudging the model toward `ix read <symbol>`

## Limitations Compared To Claude

Codex does not currently expose direct hook matchers for:
- `Grep`
- `Glob`
- `Read`
- edit preflight hooks
- post-write hooks in this repo's current Codex configuration

Because of that, the Codex port mirrors the Claude behavior semantically, but not event-for-event.

## Safety Model

The hook scripts are intentionally no-op friendly:
- if `ix` is missing, they return nothing
- if `ix status` fails, they return nothing
- if an Ix query returns no useful data, they return nothing

The hooks add context; they do not block the underlying Codex tool call.
