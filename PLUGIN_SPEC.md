# ix-codex-plugin — Plugin Specification

Version: 2.0.0-draft  
Root spec: [IX_PLUGIN_OVERHAUL_SPEC.md](../IX_PLUGIN_OVERHAUL_SPEC.md)  
Status: **Overhaul target — v1.0.0 production.** Refactor to match ix-claude-plugin behavior semantics. Pre-edit gate and post-edit ingest are the top-priority parity gaps.

---

## 1. Plugin name

`ix-memory` (Codex plugin manifest ID: `plugins/ix-memory/.codex-plugin/plugin.json`)  
Repository: `ix-codex-plugin`

---

## 2. Target AI platform

**OpenAI Codex CLI** — open-source terminal AI coding agent by OpenAI.  
GitHub: https://github.com/openai/codex  
Distribution: `install.sh --home --plugin --hooks` (global) or `install.sh --repo <path> --plugin --hooks` (per-project). Installs to `~/.codex/plugins/ix-memory/` and `~/.codex/hooks/` (or repo-local equivalents).

---

## 3. Current implementation summary

Fully operational v1.0.0 plugin. Mirrors the Claude plugin content model semantically — same seven skills, same five agents, same graph-first operating principles — but uses **Python hooks** instead of bash scripts and has fewer hook event matchers than the Claude plugin.

**Core architectural difference from Claude and Cursor:**
- Codex hooks are Python scripts, not bash
- No `Grep`/`Glob`/`Read`/edit-specific tool matchers — hook detection must happen inside generic `PreToolUse` by parsing the Bash command string
- Agents are **documentation only** — not first-class Codex runtime agents; cannot be delegated to via Codex runtime
- Plugin manifest uses `.codex-plugin/plugin.json` format, not Claude's `.claude-plugin/`

**What it does today:**
- Injects Ix operating guidance at session start via `SessionStart` hook
- Injects session briefing at prompt time via `UserPromptSubmit` hook (Ix Pro only, once per 10 min)
- Intercepts Bash commands; detects grep/rg patterns; front-runs with `ix text` + `ix locate` (Python)
- Runs `ix map` async at session end via `Stop` hook (full graph refresh)

**What is NOT wired (gaps vs Claude plugin):**
- No pre-edit impact warning (no edit-specific PreToolUse hook)
- No post-edit single-file ingest (no Write/Edit PostToolUse hook)
- No Grep/Glob tool interception (only Bash command detection)

---

## 4. Existing files and behavior to preserve

### File tree

```
ix-codex-plugin/
├── .codex/
│   ├── config.toml              # Codex workspace config
│   ├── hooks.json               # Codex hook registry
│   └── hooks/
│       ├── common.py            # Shared utilities: ix_health, caching, secret detection
│       ├── session_start.py     # SessionStart → Ix operating guidance injection
│       ├── user_prompt_submit.py # UserPromptSubmit → briefing (Pro only, 10-min gate)
│       ├── pre_tool_use.py      # PreToolUse Bash → detect grep/rg, run ix text + locate
│       └── stop.py              # Stop → async full map refresh
├── plugins/
│   └── ix-memory/
│       ├── .codex-plugin/
│       │   └── plugin.json      # Codex plugin manifest
│       └── skills/
│           ├── ix-understand/SKILL.md
│           ├── ix-investigate/SKILL.md
│           ├── ix-impact/SKILL.md
│           ├── ix-plan/SKILL.md
│           ├── ix-debug/SKILL.md
│           ├── ix-architecture/SKILL.md
│           └── ix-docs/SKILL.md
├── agents/
│   ├── ix-explorer.md           # Agent playbook (documentation; not first-class runtime)
│   ├── ix-system-explorer.md
│   ├── ix-bug-investigator.md
│   ├── ix-safe-refactor-planner.md
│   └── ix-architecture-auditor.md
├── .agents/
│   └── plugins/
│       └── marketplace.json     # Codex local plugin marketplace entry
├── scripts/
│   └── install_codex_integration.py  # Installation helper
├── AGENTS.md                    # Agent playbooks + ambient context injection
├── hooks.md                     # Hook documentation and gap analysis
├── PLUGIN_SPEC.md               # This file
├── README.md
├── install.sh                   # Bash installer
├── install.ps1                  # PowerShell installer
└── test-local.sh                # Test runner
```

### Active hook event wiring

| Script | Codex event | Detection mechanism | Behavior |
|---|---|---|---|
| `session_start.py` | `SessionStart` | — | Emits Ix operating guidance as `additionalContext` JSON |
| `user_prompt_submit.py` | `UserPromptSubmit` | — | Checks Pro status; injects `ix briefing` output if available; 10-min gate |
| `pre_tool_use.py` | `PreToolUse` | Parses Bash command string for `grep`/`rg`/`cat`/`head`/`tail` | Front-runs with `ix text <terms>` + `ix locate <terms>`; non-fatal |
| `stop.py` | `Stop` | — | Runs `ix map` async to refresh full graph |

### Python hook architecture (common.py utilities)

- `ix_health()` — checks if `ix` binary is available and responsive
- Result caching — avoids redundant `ix` calls within a session
- Secret pattern detection — prevents secret strings from being included in `ix` queries
- Event parsing — parses Codex hook event JSON from stdin

---

## 5. Known gaps and stale areas

| Gap | Impact | Priority |
|---|---|---|
| No pre-edit impact warning | Cannot warn before risky edits; core Claude ambient behavior is missing | High |
| No post-edit single-file ingest | Graph may lag significantly after edits within a session | High |
| No Grep/Glob tool interception | Cannot intercept Codex-native search tool calls (only Bash grep detection) | Medium |
| Agents are documentation only | Cannot delegate complex analysis to agents via Codex runtime | Medium |
| All hooks call `ix` CLI directly | Cannot call runtime v2 API yet | Medium |
| No `ix-help` skill/router | No skill router; user must know skill names | Low |
| Python dependency | Hooks require Python 3; adds runtime dependency vs bash-only approach | Low |
| No explicit graph staleness detection | User must manually run `ix map` when stale | Low |
| Codex hook limit: generic PreToolUse only | Cannot use fine-grained matchers; all detection in `pre_tool_use.py` | Structural |

---

## 6. Desired refactor outcome

1. **Fill the ambient behavior gaps**: Add pre-edit impact warning and post-edit ingest. These require either a Codex-native hook for edit events or a pre-tool-use parser that detects file write operations.
2. **Upgrade agents**: Investigate whether Codex supports first-class agent delegation. If so, wire the five agent playbooks as runtime agents. If not, document and keep as context-only.
3. **Migrate `ix` CLI calls to runtime API**: Replace all Python subprocess `ix` calls with HTTP calls to `POST /v2/ix_query` and related endpoints.
4. **Verify and document Codex MCP support**: Codex CLI recently added MCP. If stable, expose Ix tools as MCP tools (same model as Cursor). This would be the most impactful capability upgrade.
5. **Add `ix-help` skill router** matching the Claude and Cursor implementations.

---

## 7. Platform-specific integration model

Codex CLI's plugin system provides:
- **Plugin manifest** — `.codex-plugin/plugin.json` in the plugin directory
- **Hooks** — Python scripts registered in `.codex/hooks.json`; events: `SessionStart`, `UserPromptSubmit`, `PreToolUse` (generic), `Stop`
- **Skills** — loaded from `plugins/ix-memory/skills/`; format matches Claude skills (`.md` files with frontmatter)
- **Agents** — status as first-class runtime agents Unknown / needs verification; currently docs-only
- **MCP** — recently added; scope, transport, and config format need verification

**Known limitations vs Claude:**
- No `Grep`, `Glob`, or `Read` tool matchers in `PreToolUse`
- No `Write`/`Edit`/`MultiEdit` PostToolUse events (no post-edit ingest hook)
- No `beforeSubmitPrompt` equivalent (only `UserPromptSubmit` which fires after the user submits)
- Hooks are Python, not bash (different from Claude's bash hook architecture)

**Installation modes:**
```bash
# Global install
./install.sh --home --plugin --hooks

# Per-repo install
./install.sh --repo /path/to/project --plugin --hooks

# Symlink mode (dev)
./install.sh --repo /path/to/project --plugin --hooks --mode symlink
```

---

## 8. Required Ix capabilities

| Capability | Current implementation | Target implementation |
|---|---|---|
| Session guidance | `session_start.py` outputs static guidance text | `POST /v2/ix_query` mode `"status"` dynamic guidance |
| Briefing (Pro) | `user_prompt_submit.py` → `ix briefing` CLI | `POST /v2/ix_query` mode `"status"` or Pro-tier endpoint |
| Search interception | `pre_tool_use.py` → `ix text` + `ix locate` CLI | `POST /v2/ix_query` mode `"locate"` |
| Pre-edit gate | **Not implemented** | `POST /v2/ix_decide` via Bash parse or MCP tool |
| Post-edit ingest | **Not implemented** | `POST /v2/ingest/map` via Bash parse or MCP tool |
| Full map refresh | `stop.py` → `ix map` CLI | `POST /v2/ingest/map` full workspace |
| All seven skills | Direct `ix` CLI calls | `POST /v2/ix_query` with appropriate mode |
| Agent delegation | Not supported (docs only) | TBD pending Codex agent runtime verification |
| MCP tools | Not available (needs verification) | If MCP confirmed: 17 tools mirroring Cursor plugin |

---

## 9. Required hooks, skills, agents, commands, and MCP integrations

### Hooks required (current + additions)

| Behavior | Hook | Status | Notes |
|---|---|---|---|
| Session guidance | `session_start.py` | Exists | Migrate to runtime API |
| Briefing | `user_prompt_submit.py` | Exists | Migrate to runtime API |
| Search interception | `pre_tool_use.py` (Bash detect) | Exists | Migrate to runtime API |
| Full map refresh | `stop.py` | Exists | Migrate to runtime API |
| Pre-edit gate | New hook or enhanced `pre_tool_use.py` | **Missing** | Detect Write/Edit operations in Bash; call `ix_decide` |
| Post-edit ingest | New hook or enhanced `stop.py` | **Missing** | Detect edited files; call `ingest/map` per-file |

### Skills required

Seven skills (all exist; need runtime API update):
`ix-understand`, `ix-investigate`, `ix-impact`, `ix-plan`, `ix-debug`, `ix-architecture`, `ix-docs`

Add: `ix-help` skill router (currently missing).

### Agents required

Five agents (docs exist; runtime status unknown):
`ix-explorer`, `ix-system-explorer`, `ix-bug-investigator`, `ix-safe-refactor-planner`, `ix-architecture-auditor`

If Codex supports first-class agents: wire agent playbooks as Codex runtime agents. If not: keep docs in `agents/`; document limitation in `hooks.md`.

### MCP

Verify Codex MCP support. If available:
- Register Ix as MCP server (same 17 tools as Cursor plugin)
- Replace Python hook subprocess calls with MCP tool calls
- Document config format for `.codex/` MCP registration

---

## 10. Required folder structure after refactor

```
ix-codex-plugin/
├── .codex/
│   ├── config.toml
│   ├── hooks.json               # Updated to include new pre-edit and post-edit hooks
│   └── hooks/
│       ├── common.py            # Updated: add runtime HTTP client
│       ├── session_start.py     # Updated: call runtime API
│       ├── user_prompt_submit.py # Updated: call runtime API
│       ├── pre_tool_use.py      # Updated: call runtime API + detect write ops for pre-edit gate
│       ├── post_tool_use.py     # NEW: detect edited files; call ingest/map per-file
│       └── stop.py              # Updated: call runtime API
├── plugins/
│   └── ix-memory/
│       ├── .codex-plugin/
│       │   └── plugin.json
│       └── skills/
│           ├── ix-help/SKILL.md # NEW: skill router
│           ├── ix-understand/SKILL.md
│           ├── ix-investigate/SKILL.md
│           ├── ix-impact/SKILL.md
│           ├── ix-plan/SKILL.md
│           ├── ix-debug/SKILL.md
│           ├── ix-architecture/SKILL.md
│           └── ix-docs/SKILL.md
├── agents/
│   └── [five agent playbooks — unchanged]
├── mcp/                         # NEW (if MCP is confirmed available)
│   ├── server.py                # Python MCP server (mirrors Cursor TypeScript server)
│   └── tools/                   # 17 Python tool implementations
├── scripts/
│   └── install_codex_integration.py
├── AGENTS.md
├── hooks.md                     # Updated with gap analysis and MCP verification findings
├── PLUGIN_SPEC.md               # This file
├── README.md
├── install.sh, install.ps1
└── test-local.sh
```

---

## 11. Shared Ix Core Runtime requirements

See [IX_PLUGIN_OVERHAUL_SPEC.md](../IX_PLUGIN_OVERHAUL_SPEC.md). Plugin-specific notes:

- `caller.surface = "codex-plugin"` in all API calls.
- Python HTTP client must be added to `common.py`; must handle `IX_UPSTREAM_UNAVAILABLE` gracefully (bail silently).
- If MCP is available: use stdio transport; Python MCP client from `modelcontextprotocol` SDK.
- Pre-edit gate via `pre_tool_use.py` must parse Bash commands to detect file writes and extract target paths.

---

## 12. API contracts used by this plugin

| API | Hook/skill | Current | Target |
|---|---|---|---|
| `POST /v2/ix_query` mode `"locate"` | `pre_tool_use.py` | `ix text` + `ix locate` CLI | Runtime HTTP |
| `POST /v2/ix_query` mode `"status"` | `session_start.py`, `user_prompt_submit.py` | Static text / `ix briefing` CLI | Runtime HTTP |
| `POST /v2/ix_decide` | `pre_tool_use.py` (write detection) | **Not implemented** | Runtime HTTP |
| `POST /v2/ingest/map` | `post_tool_use.py`, `stop.py` | `stop.py` → `ix map` CLI only | Runtime HTTP |
| `GET /v2/status` | `common.py` health check | `ix` binary check | Runtime HTTP |
| `POST /v2/ix_query` mode (per skill) | Each skill | `ix` CLI commands | Runtime HTTP |

---

## 13. Security and privacy requirements

- Python hook scripts must remain thin wrappers — no business logic beyond routing.
- `common.py` secret pattern detection must run before any string is submitted to Ix.
- Plugin manifest must not include secrets, tokens, or machine-specific paths.
- Python HTTP client must not log raw source code or prompt text in telemetry.
- `AGENTS.md` must not include secrets or machine-specific paths.

---

## 14. Testing requirements

Run: `bash test-local.sh`

| Test | Coverage |
|---|---|
| All existing hook integration tests | Must pass against v2 runtime |
| `CodexPreEditGateFeasibility` | Validates `pre_tool_use.py` reliably detects Write/Edit operations in Bash and calls `ix_decide` |
| `CodexPostEditIngestFeasibility` | Validates post-edit ingest fires after file writes |
| `CodexMcpAvailability` | Documents MCP availability (pass if confirmed, skip-and-document if not) |
| `CodexAgentDelegation` | Documents agent delegation status (first-class or docs-only) |
| `RuntimeUnavailableFallback` | All hooks bail silently when runtime is unavailable |
| Shared golden cases | `UnderstandLargeMonorepo`, `ImpactCrossBoundaryEdit`, `DebugWithStaleClaim` |

---

## 15. Migration plan

| Step | Action | Risk |
|---|---|---|
| 1. Freeze hook output fixtures | Capture current hook outputs for all four hooks | Low |
| 2. Verify MCP support | Run `codex` with MCP config; confirm transport and tool call behavior | High (unknown) |
| 3. Verify agent delegation | Check if Codex supports first-class agent runtime | Medium (unknown) |
| 4. Add HTTP client to `common.py` | Implement `call_runtime(endpoint, payload)` with fallback | Low |
| 5. Migrate `stop.py` | Switch full-map refresh to `POST /v2/ingest/map` | Low |
| 6. Migrate search interception | Switch `pre_tool_use.py` search to `POST /v2/ix_query` mode `"locate"` | Medium |
| 7. Add pre-edit gate | Extend `pre_tool_use.py` to detect write ops; call `POST /v2/ix_decide` | Medium-high |
| 8. Add post-edit ingest | Write `post_tool_use.py`; call `POST /v2/ingest/map` per touched file | Medium |
| 9. Migrate session/briefing hooks | Switch to runtime API | Medium |
| 10. Add MCP server (if confirmed) | Implement `mcp/server.py` with 17 tools matching Cursor plugin | High |
| 11. Add `ix-help` skill | Write skill router | Low |
| 12. Dual-run validation | Compare old/new hook behavior against fixtures | High |

---

## 16. Acceptance criteria

- [ ] All four Python hooks call the Ix Core Runtime API; no direct `ix` CLI subprocess calls remain.
- [ ] Pre-edit impact warning is implemented and fires before Bash file write operations.
- [ ] Post-edit ingest fires after detected file write operations.
- [ ] `ix-help` skill router is added.
- [ ] MCP support status is verified and documented (either implemented or formally documented as unavailable).
- [ ] Agent delegation status is verified and documented.
- [ ] All hooks degrade gracefully when runtime is unavailable.
- [ ] Shared golden cases pass.
- [ ] `test-local.sh` passes with zero failures.

---

## 17. Open questions

1. Does Codex CLI support MCP (stdio, HTTP, or both)? What is the config format?
2. Does Codex support first-class runtime agents, or only documentation-style agent playbooks?
3. Can `pre_tool_use.py` reliably detect file write operations from the Bash command string? (e.g., detect `>` redirections, `tee`, editor invocations)
4. Is there a `PostToolUse` hook event in Codex, or must post-edit detection also be done inside `PreToolUse` or `Stop`?
5. Does the `SessionStart` hook fire reliably before the first user prompt (i.e., can it be used to probe runtime health)?
6. Does Codex support a `beforeSubmitPrompt` equivalent for lower-latency context injection vs `UserPromptSubmit`?


---

## Roadmap

See [`ROADMAP.md`](./ROADMAP.md) for phased implementation tasks and progress tracking.
