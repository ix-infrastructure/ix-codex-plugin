# ix-codex-plugin

A Codex plugin that turns Codex into a graph-reasoning engineering agent using [Ix Memory](https://github.com/ix-infrastructure/IX-Memory) as its structured memory backend.

Codex + Ix = reasoning engine + persistent code knowledge graph. Skills are cognitive abstractions, not CLI wrappers.

## What This Repo Replicates

This repo now mirrors the `ix-claude-plugin` content model as closely as Codex currently allows:
- the same seven high-level cognitive skills
- Codex-specific helper skills for routing and onboarding
- the same graph-first operating guidance
- the same agent playbooks, shipped here as reusable docs under `agents/`
- hook behavior that front-runs shell search/read actions with Ix context

Codex runtime limitation:
- Codex does not currently expose Claude-style hook matchers for `Grep`, `Glob`, `Read`, edit preflight hooks, or the Claude plugin manifest format.
- Because of that, the Codex port matches the Claude plugin semantically, but not event-for-event.

## Requirements

- [Ix Memory](https://github.com/ix-infrastructure/IX-Memory) installed and running (`ix status` returns ok)
- `python3` in PATH for the installer and hook scripts
- `ripgrep` (`rg`) is recommended

Ix Pro is optional. If present, the `UserPromptSubmit` hook injects the Ix session briefing once per 10 minutes, matching the Claude plugin behavior.

## Skills

Codex registers every skill in [`plugins/ix-memory/skills/`](./plugins/ix-memory/skills/). Use the `$`-prefixed skill name in chat, for example `$ix-tutorial` or `$ix-understand`.

Core analysis skills:

| Skill | What it does | Key rule |
|-------|--------------|----------|
| `$ix-understand [target]` | Build a mental model of a system or the whole repo | Graph only; no code reads |
| `$ix-investigate <symbol>` | Deep dive: what it is, how it connects, execution path | Graph first; one symbol read max |
| `$ix-impact <target>` | Change risk: blast radius, affected systems, test targets | Depth scales with risk |
| `$ix-plan <targets...>` | Risk-ordered implementation plan for a set of changes | Parallel impact; finds shared dependents |
| `$ix-debug <symptom>` | Root cause analysis from symptom to candidates | Minimal source reads at suspects only |
| `$ix-architecture [scope]` | Design health: coupling, smells, hotspots | Graph only; never reads source |
| `$ix-docs <target> [--full] [--style narrative|reference|hybrid] [--split] [--single-doc] [--out <path>]` | Generate narrative-first documentation with a selective reference layer | Default is onboarding-focused; `--full --style hybrid` goes deepest |

Helper skills:

| Skill | What it does |
|-------|--------------|
| `$ix-help <task or question>` | Routes a request to the best Ix skill or raw command |
| `$ix-tutorial [topic]` | Explains how to use the plugin in Codex with copy-paste examples |

## How To Invoke Skills In Codex

Codex skill invocation uses a `$`-prefixed skill name. Start your prompt with the registered skill name, then add the task or target inline.

Local Codex plugins do not currently guarantee slash-command popups or `/skill` autocomplete for these skills, so `$ix-*` is the reliable primary UX.

Recommended patterns:
- `$ix-help how does the auth subsystem work?`
- `$ix-understand this repo`
- `$ix-investigate IxClient`
- `$ix-impact Ix/ix-cli/src/client/api.ts`
- `$ix-plan session_start.py common.py`
- `$ix-debug ContextService query flow`
- `$ix-tutorial how do I use the ix-memory plugin in Codex?`

For exact lookups, raw `ix` commands are still appropriate:
- `Run ix locate IxClient --format llm`
- `Run ix callers IxClient --limit 15 --format llm`

## Agent Playbooks

For parity with `ix-claude-plugin`, this repo also ships the same playbooks in [`agents/`](./agents):

| Playbook | Purpose |
|----------|---------|
| `ix-explorer` | General-purpose graph exploration |
| `ix-system-explorer` | Full architectural model of a codebase or region |
| `ix-bug-investigator` | Root cause analysis from symptom to candidates |
| `ix-safe-refactor-planner` | Blast radius plus safe change sequencing |
| `ix-architecture-auditor` | Structural health report with ranked improvements |

These are documentation artifacts today. Codex local plugins do not currently install them as first-class custom agents through `.codex-plugin/plugin.json`.

## Automatic Hooks

| Trigger | Codex hook | Effect |
|---------|------------|--------|
| Codex session starts | `SessionStart` | Injects Ix operating guidance and the graph-first rules |
| User sends a prompt | `UserPromptSubmit` | Injects `ix briefing` once per 10 min if Ix Pro is available |
| Codex runs `Bash` with `grep`/`rg` | `PreToolUse` | Front-runs with `ix text` plus `ix locate` and injects a concise summary |
| Codex runs `Bash` with read-style commands (`cat`, `sed`, `head`, `tail`, `awk`) | `PreToolUse` | Front-runs with `ix inventory`, `ix overview`, and `ix impact` for the target file |
| Codex finishes responding | `Stop` | Runs `ix map` asynchronously to refresh the graph |

Unsupported Claude-only hook points today:
- `Grep`
- `Glob`
- `Read`
- edit preflight hooks
- write post-hooks in the current Codex hook bundle

## Install

### Quick install

```bash
curl -fsSL https://raw.githubusercontent.com/ix-infrastructure/ix-codex-plugin/main/codex-install.sh | sh
```

Then restart Codex and install or enable `ix-memory` from the `ix-codex-plugin` marketplace.
Running the installer only copies/registers the plugin; the skills do not appear until the plugin is enabled in Codex.

PowerShell:

```powershell
irm https://raw.githubusercontent.com/ix-infrastructure/ix-codex-plugin/main/codex-install.ps1 | iex
```

The hosted installers cache the source checkout in `~/.ix/codex-plugin-source` and default to
`--home --plugin --hooks --mcp`.

If you only want the plugin and not the hooks:

```bash
./install.sh --home --plugin
```

If you only want a repo-local install:

```bash
./install.sh --repo /path/to/project --plugin --hooks --mcp
```

If you want a fully local checkout for development or advanced flags, clone the repo and use the
local wrappers:

```bash
git clone https://github.com/ix-infrastructure/ix-codex-plugin.git
cd ix-codex-plugin
./install.sh --home --plugin --hooks --mcp
```

### What gets installed

Plugin:
- `plugins/ix-memory/.codex-plugin/plugin.json`
- `plugins/ix-memory/skills/*`
- `.agents/plugins/marketplace.json`

The plugin install step registers a marketplace entry. It does not auto-enable `ix-memory`; restart Codex and enable the plugin before expecting its skills to show up.

Hooks:
- `.codex/config.toml`
- `.codex/hooks.json`
- `.codex/hooks/common.py`
- `.codex/hooks/session_start.py`
- `.codex/hooks/user_prompt_submit.py`
- `.codex/hooks/pre_tool_use.py`
- `.codex/hooks/stop.py`

MCP:
- `.codex/mcp/server.py`

### Home-local install

```bash
./install.sh --home --plugin --hooks --mcp --mode copy
```

This writes:
- `~/.codex/plugins/ix-memory`
- `~/.agents/plugins/marketplace.json`
- `~/.codex/config.toml`
- `~/.codex/hooks.json`
- `~/.codex/hooks/*.py`
- `~/.codex/mcp/server.py`

### Repo-local install

```bash
./install.sh --repo /path/to/project --plugin --hooks --mcp --mode copy
```

This writes:
- `/path/to/project/plugins/ix-memory`
- `/path/to/project/.agents/plugins/marketplace.json`
- `/path/to/project/.codex/config.toml`
- `/path/to/project/.codex/hooks.json`
- `/path/to/project/.codex/hooks/*.py`
- `/path/to/project/.codex/mcp/server.py`

### Symlink mode for local development

```bash
./install.sh --repo /path/to/project --plugin --hooks --mcp --mode symlink
```

### Help

```bash
./install.sh --help
```

```powershell
.\install.ps1 --help
```

## Manual Install

Codex also supports manual local plugin installation through a marketplace file.

### Repo marketplace

1. Copy `plugins/ix-memory` into `<repo>/plugins/ix-memory`.
2. Add or update `<repo>/.agents/plugins/marketplace.json` with an entry pointing to `./plugins/ix-memory`.
3. Restart Codex.
4. Install `ix-memory` from that repo marketplace.

### Personal marketplace

1. Copy `plugins/ix-memory` into `~/.codex/plugins/ix-memory`.
2. Add or update `~/.agents/plugins/marketplace.json` with `source.path` pointing to `./.codex/plugins/ix-memory`.
3. Restart Codex.
4. Install `ix-memory`.

### Hooks without the installer

Copy these into either the repo or `~/.codex`:
- `.codex/config.toml`
- `.codex/hooks.json`
- `.codex/hooks/common.py`
- `.codex/hooks/session_start.py`
- `.codex/hooks/user_prompt_submit.py`
- `.codex/hooks/pre_tool_use.py`
- `.codex/hooks/stop.py`

## Verify active plugin version

Each `--hooks` install writes a small metadata file at `.codex/ix-plugin-version.json` in the
target directory. This lets you confirm exactly which build of the plugin is active.

```bash
cat .codex/ix-plugin-version.json
```

Example output:

```json
{
  "plugin_name": "ix-memory",
  "plugin_version": "2.3.0",
  "source_path": "/home/you/ix-codex-plugin",
  "installed_at": "2026-04-29T18:00:00+00:00",
  "git_commit": "a1b2c3d4e5f6..."
}
```

At the start of every Codex session the `SessionStart` hook reads this file and emits a
one-line header into the session context:

```
ix-memory plugin v2.3.0 active | installed 2026-04-29 | commit a1b2c3d
```

**To confirm a reinstall took effect:**

1. Reinstall: `./install.sh --repo /path/to/project --hooks --force`
2. Check the new timestamp: `cat /path/to/project/.codex/ix-plugin-version.json`
3. Start a new Codex session — the version line in the session context will reflect the new
   install date and commit.

If the version line is missing, the hooks were installed manually without using the installer.
Run `./install.sh --repo /path/to/project --hooks --force` to write the file.

## Repo Guidance

The repo-level operating guide lives in [`AGENTS.md`](./AGENTS.md). It carries the Claude plugin's graph-first reasoning model, skill reference, token-budget rules, and Codex-specific notes about hook/runtime differences.
