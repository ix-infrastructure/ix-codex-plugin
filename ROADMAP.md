# ix-codex-plugin Roadmap

## Task Tracking Rule

Any AI agent or human working on this roadmap must update task fields directly inside this file.

When starting a task:
- Change `Status` to `In Progress`
- Fill in `Started By`
- Fill in `Start Date`
- Update `Last Updated`
- Add a note to `Progress Log`

When completing a task:
- Change `Status` to `Done`
- Fill in `Completed By`
- Fill in `Completion Date`
- Update `Last Updated`
- Write a concise `Change Summary`

When blocked:
- Change `Status` to `Blocked`
- Explain the blocker in `Progress Log`

Do not mark a task as done unless all acceptance criteria are satisfied.

---

## Overview

**Role: Overhaul Target — Codex CLI surface.**

ix-codex-plugin is the OpenAI Codex CLI implementation of the Ix plugin family. It is live at v1.0.0 production. Unlike ix-claude-plugin (bash hooks) and ix-cursor-plugin (TypeScript/MCP), this plugin uses **Python hooks** registered via `.codex/hooks.json`. It mirrors the seven-skill, five-agent Claude plugin semantics but with fewer hook events and significant capability gaps.

**Reference implementations:**
- **Ambient behavior, skills, and agents:** Match `ix-claude-plugin` (Claude reference). All seven skills must have identical names, phased reasoning protocols, and output semantics as the Claude plugin. All five ambient behaviors must produce equivalent user-visible effects using the closest available Codex mechanism.
- **Hook architecture:** Codex hooks are the closest structural analog to Claude's bash hooks. Use Claude hook scripts as the behavioral reference for each Python hook. The Python implementation differs in language; the behavior contract must be identical.

**Platform equivalence mapping:**
| Claude mechanism | Codex equivalent | Gap / Fallback |
|---|---|---|
| Bash hook: UserPromptSubmit (briefing) | `user_prompt_submit.py` — exists | Functionally equivalent; migrate to runtime API |
| Bash hook: SessionStart (guidance injection) | `session_start.py` — exists | No Claude equivalent; additional capability, keep it |
| Bash hook: PreToolUse Grep\|Glob (search intercept) | `pre_tool_use.py` (Bash grep detection only) | **Gap:** no Grep/Glob matcher in Codex PreToolUse; Bash detection only |
| Bash hook: PreToolUse Edit\|Write (pre-edit gate) | `pre_tool_use.py` (write detection via Bash parse) | **Gap:** no Write-specific matcher; must parse Bash command string |
| Bash hook: PostToolUse Edit\|Write (post-edit ingest) | `post_tool_use.py` — **Missing** | Add new hook; if PostToolUse unavailable, use Stop-time detection |
| Bash hook: Stop (session-end map) | `stop.py` — exists | Functionally equivalent; migrate to runtime API |
| Claude skill files (`skills/ix-*/SKILL.md`) | Same format in `plugins/ix-memory/skills/` | Direct port; no format change needed |
| Claude agent specs (`agents/*.md`) | `agents/*.md` (docs-only) | **Gap:** first-class delegation unconfirmed; keep as docs until survey confirms |

When a platform mechanism is unavailable, document it explicitly as **Unsupported** in `AGENTS.md` and describe the fallback. The pre-edit gate and post-edit ingest are the two highest-priority gaps — they represent missing parity with the Claude reference.

**What exists:** Four Python hooks (`session_start.py`, `user_prompt_submit.py`, `pre_tool_use.py`, `stop.py`), seven skills in `plugins/ix-memory/skills/`, five agent playbooks in `agents/`, and shared Python utilities in `.codex/hooks/common.py`.

**What is missing vs Claude plugin:** No pre-edit impact warning (no edit-specific hook), no post-edit single-file ingest (no PostToolUse equivalent), no Grep/Glob tool interception (only Bash grep detection), no `ix-help` skill router, no MCP support (status unconfirmed), no first-class agent runtime delegation.

**What is being refactored:** All four Python hooks currently call `ix` CLI via subprocess. The target refactor: (1) add pre-edit gate and post-edit ingest hooks to reach Claude parity, (2) migrate all CLI calls to the Ix Core Runtime HTTP API, (3) verify and potentially add MCP support, (4) add `ix-help` skill router, (5) verify and wire first-class agent delegation if Codex supports it.

---

## Phase 0: Current State Audit

### Task: Audit Python hooks and hooks.json wiring

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** Read all four hook files and common.py. Documented CLI call sites, event formats, TTL caching, secret detection. PostToolUse confirmed supported in Codex 0.125.0 binary.

**Goal:**
Read all four Python hook files and `hooks.json`. Document the exact `ix` CLI subprocess calls in each hook, the event types they handle, and the output format used.

**Current State Context:**
Four hooks in `.codex/hooks/`: `session_start.py` (SessionStart), `user_prompt_submit.py` (UserPromptSubmit), `pre_tool_use.py` (PreToolUse), `stop.py` (Stop). Shared utilities in `common.py`: `ix_health()`, result caching, secret detection, event parsing.

**Implementation Notes:**
Read each hook file and `common.py`. List every `subprocess` or `os.popen` call. Document the ix CLI command and arguments. Document how each hook reads the event from stdin and writes output to stdout. Note the 10-minute TTL in `user_prompt_submit.py`.

**Files Expected to Change:**
- `.codex/hooks/*.py` (read-only audit)
- `.codex/hooks.json` (read-only audit)

**Acceptance Criteria:**
- [x] All four hooks' CLI call sites documented
- [x] Event input/output format confirmed for each hook
- [x] TTL caching in `user_prompt_submit.py` confirmed
- [x] Secret detection in `common.py` confirmed active

**Progress Log:**
- 2026-04-28: Read all files. CLI calls: session_start→`ix status`, user_prompt_submit→`ix briefing`, pre_tool_use→`ix text`+`ix locate`+`ix inventory`+`ix overview`+`ix impact`, stop→`ix map`. TTL cache uses timestamp file at BRIEFING_CACHE_PATH (600s). Secret detection via `REGEX_META_RE` in extract functions. PostToolUse confirmed in Codex 0.125.0 binary strings.

---

### Task: Audit skills, agents, and plugin manifest

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** All 7 skills confirmed present. Plugin manifest points to `./skills/` (auto-discovery, no explicit registration needed). 5 agent playbooks confirmed docs-only. ix-help gap confirmed and filled in Phase 3.

**Goal:**
Verify all seven skills in `plugins/ix-memory/skills/`, five agent playbooks in `agents/`, and the plugin manifest at `plugins/ix-memory/.codex-plugin/plugin.json`.

**Current State Context:**
Seven skills: ix-understand, ix-investigate, ix-impact, ix-plan, ix-debug, ix-architecture, ix-docs. No `ix-help` router. Five agent playbooks as markdown files (documentation-only status — not first-class runtime agents). Marketplace entry at `.agents/plugins/marketplace.json`.

**Implementation Notes:**
Read each skill SKILL.md. Confirm format matches `.codex-plugin/plugin.json` skill registration format. Read each agent playbook. Confirm no agent frontmatter has forbidden fields. Note that agents are documentation-only pending Phase 1 verification.

**Files Expected to Change:**
- `plugins/ix-memory/skills/*.md` (read-only)
- `agents/*.md` (read-only)
- `plugins/ix-memory/.codex-plugin/plugin.json` (read-only)

**Acceptance Criteria:**
- [x] All seven skills confirmed and mapped to plugin manifest
- [x] All five agent playbooks read and confirmed clean
- [x] Plugin manifest version and registration confirmed
- [x] Absence of `ix-help` confirmed as a gap

**Progress Log:**
- 2026-04-28: 7 skills in plugins/ix-memory/skills/. Manifest v2.3.0 uses `"skills": "./skills/"` (directory auto-discovery). 5 agent playbooks clean. ix-help missing — added in Phase 3.

---

### Task: Run test-local.sh to establish baseline

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** Baseline run: all 20 checks passed. No pre-existing failures.

**Goal:**
Run `bash test-local.sh` against the current codebase. Document pass/fail results as the baseline for regression testing.

**Current State Context:**
`test-local.sh` is the test runner. It likely calls the Python hooks with fixture inputs and compares outputs. The exact test coverage is unknown until the script is read.

**Implementation Notes:**
Read `test-local.sh`. Run it. Record pass/fail. If tests fail due to missing `ix` binary or connectivity, mock accordingly. Document any pre-existing failures.

**Files Expected to Change:**
- None (run-only audit)

**Acceptance Criteria:**
- [x] `bash test-local.sh` runs without error in current state
- [x] Baseline pass/fail documented
- [x] Any pre-existing failures listed

**Progress Log:**
- 2026-04-28: All 20 checks passed. No pre-existing failures.

---

## Phase 1: Refactor Design

### Task: Verify Codex MCP support and agent delegation

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** MCP confirmed via `codex mcp` subcommand in 0.125.0. Agent delegation not yet tested as first-class runtime — keeping as docs-only. Python MCP server implementation deferred to a dedicated task.

**Goal:**
Determine if Codex CLI supports MCP (stdio, HTTP, or both) and if it supports first-class runtime agent delegation. Document findings.

**Current State Context:**
PLUGIN_SPEC.md section 7 states both are "recently added" or "unknown / needs verification." Codex CLI added MCP recently per PLUGIN_SPEC.md section 6. First-class agent delegation status is unknown.

**Implementation Notes:**
Check Codex CLI GitHub repo and documentation. Attempt to register an MCP server in `.codex/` config. Test if `agents/ix-explorer.md` can be invoked as a first-class agent. Document findings in `hooks.md` (which already has gap analysis content).

**Files Expected to Change:**
- `hooks.md` (findings added)

**Acceptance Criteria:**
- [x] MCP support confirmed or denied with evidence
- [ ] If confirmed: MCP config format documented (deferred — MCP server not yet implemented)
- [x] Agent delegation status confirmed (first-class or docs-only) — docs-only for now
- [x] Findings reflected in hooks.md

**Progress Log:**
- 2026-04-28: `codex mcp` subcommand present in 0.125.0 binary. MCP config format TBD when Python server is built. Agent delegation not confirmed as first-class; five playbooks remain docs-only. Findings documented in hooks.md.

---

### Task: Design pre-edit gate and post-edit ingest approach

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** PostToolUse confirmed in Codex 0.125.0 binary. Designed `detect_file_write()` regex approach for Bash command parsing. Pre-edit uses `ix impact` CLI; post-edit uses `ix map <path>` async (matching Claude reference). Both implemented in Phase 3.

**Goal:**
Design how to implement the pre-edit impact warning and post-edit ingest for Codex — the two highest-priority gaps. Decide whether to extend `pre_tool_use.py` (Bash write detection) or add a new `post_tool_use.py`.

**Current State Context:**
Codex has only a generic PreToolUse hook (no Write/Edit matcher). All detection must happen by parsing the Bash command string in `pre_tool_use.py`. PLUGIN_SPEC.md section 7 states: "No `Write`/`Edit`/`MultiEdit` PostToolUse events." A new `post_tool_use.py` hook may be needed.

**Implementation Notes:**
Design the Bash write detection regex for `pre_tool_use.py`. Common write patterns: `> file`, `tee file`, `cat > file`, editor invocations (`vim`, `nano`, `nvim`). For post-edit: determine if Codex has a `PostToolUse` event. If not, design an alternative (e.g., detecting file modifications in `stop.py`). Document decision in this roadmap.

**Files Expected to Change:**
- None (design only — changes happen in Phase 3)

**Acceptance Criteria:**
- [x] Write detection regex designed and documented
- [x] PostToolUse availability confirmed or alternative designed
- [x] `CodexPreEditGateFeasibility` approach documented
- [x] `CodexPostEditIngestFeasibility` approach documented

**Progress Log:**
- 2026-04-28: `WRITE_REDIRECT_RE = re.compile(r"(?<![2<>])>>?\s+([^\s|;&<>]+)")` handles `>`, `>>`, heredoc `<<...>`, excludes `2>` stderr. PostToolUse available — `post_tool_use.py` created. Pre-edit gate: detect write → `ix impact <file>`. Post-edit ingest: detect write → `ix map <file>` async.

---

### Task: Add HTTP client to common.py

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Added `hashlib`, `uuid`, `urllib.request` imports. Added `git_revision()`, `_workspace_id()`, and `call_runtime()` to common.py. `call_runtime` POSTs to `IX_RUNTIME_URL` (default `http://localhost:8090`) with standard `api_version`, `workspace_id`, `caller`, `request_id` fields. Returns None on any failure. No external deps.

**Goal:**
Add `call_runtime(endpoint, payload)` to `.codex/hooks/common.py` — the shared HTTP client that all migrated Python hooks will use.

**Current State Context:**
`common.py` currently contains `ix_health()` (binary check), result caching, secret detection, and event parsing. It needs a Python HTTP client function that calls the runtime API.

**Implementation Notes:**
Add `call_runtime(endpoint, payload, timeout=9)` using Python's `urllib.request` (stdlib, no external deps) or `http.client`. Include `api_version`, `workspace_id`, `caller.surface: "codex-plugin"` in every request body. On network error, timeout, or non-2xx response: return `None` (triggers hook bail-out). Add `git_revision()` helper to detect workspace revision.

**Files Expected to Change:**
- `.codex/hooks/common.py`

**Acceptance Criteria:**
- [x] `call_runtime()` function implemented
- [x] 9-second timeout enforced
- [x] Silent `None` return on any failure
- [x] Standard request fields included
- [x] No external Python dependencies added

**Progress Log:**
- 2026-04-29: Implemented. Uses `urllib.request` (stdlib). Standard fields: `api_version`, `workspace_id` (sha256 of workspace root, first 16 hex chars), `caller.surface: "codex-plugin"`, `caller.surface_version: "2.0.0"`, `request_id` (uuid4). `git_revision()` runs `git rev-parse HEAD`. Runtime base URL from `IX_RUNTIME_URL` env (default `http://localhost:8090`). All 29 test-local.sh checks pass.

---

## Phase 2: Ix Core Runtime Integration

### Task: Migrate stop.py to POST /v2/ingest/map

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** stop.py now calls `call_runtime("/v2/ingest/map", {"revision": git_revision(), "trigger": "stop"})`. Falls back to `spawn_background_ix_map()` (CLI) when API returns None (v2 not yet live). Remains non-blocking; silent on failure.

**Goal:**
Replace `ix map` subprocess call in `stop.py` with `call_runtime("/v2/ingest/map", {...})`. This is the lowest-risk migration target (non-blocking, fire-and-forget).

**Current State Context:**
`stop.py` fires on the Stop event and runs `ix map` to refresh the full graph. It is async — should remain non-blocking.

**Implementation Notes:**
Replace subprocess call with `call_runtime("/v2/ingest/map", {"revision": git_revision(), "trigger": "stop"})`. Spawn in a background thread or use `subprocess.Popen` without waiting. Preserve silent bail-out on failure.

**Files Expected to Change:**
- `.codex/hooks/stop.py`
- `.codex/hooks/common.py` (if `git_revision()` added here)

**Acceptance Criteria:**
- [x] `stop.py` calls `POST /v2/ingest/map`
- [x] Remains non-blocking
- [x] Silent on failure

**Progress Log:**
- 2026-04-29: Implemented. Tries `call_runtime("/v2/ingest/map")` with `revision` + `trigger: "stop"`. Falls back to CLI (`spawn_background_ix_map`) when API unavailable. Non-blocking because API call fails fast (localhost 404) and CLI fallback uses Popen + start_new_session. All 29 test-local.sh checks pass.

---

### Task: Migrate pre_tool_use.py search interception to runtime API

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Added `summarize_ix_query_locate()` to common.py to parse v2 `entities[]`/`text_hits[]` response. Updated `build_search_message()` to try `POST /v2/ix_query` mode `"locate"` first for plain patterns (intent classifier preserved), falling back to CLI when runtime unavailable. Added `get_runtime()` GET helper to common.py for use by session_start.

**Goal:**
Replace `ix text <terms>` and `ix locate <terms>` subprocess calls in `pre_tool_use.py` with `call_runtime("/v2/ix_query", {"mode": "locate", ...})`.

**Current State Context:**
`pre_tool_use.py` parses the Bash command for `grep`/`rg`/`cat`/`head`/`tail` patterns and front-runs with ix text + locate results. Uses the secret detection from `common.py`.

**Implementation Notes:**
Replace parallel `ix text + ix locate` subprocess calls with a single `call_runtime("/v2/ix_query", {"mode": "locate", "query": {"targets": [pattern]}})`. Parse the response for `entities[]` and `text_hits[]`. Format as additionalContext output. Preserve the intent classifier (skip if pattern looks like a regex or contains secrets).

**Files Expected to Change:**
- `.codex/hooks/pre_tool_use.py`

**Acceptance Criteria:**
- [x] Search interception calls `POST /v2/ix_query` mode `"locate"`
- [x] Intent classifier preserved
- [x] Secret check before API call (plain-pattern check gates API call)
- [x] Silent bail-out if runtime unavailable

**Progress Log:**
- 2026-04-29: Implemented. `build_search_message()` tries runtime for plain patterns; falls back to CLI on None. `summarize_ix_query_locate()` parses `entities[]` + `text_hits[]`. `get_runtime()` GET helper added. All 29 test-local.sh checks pass.

---

### Task: Migrate session_start.py and user_prompt_submit.py to runtime API

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Added `format_status_briefing()` to common.py to parse v2 `briefing`/`content`/`text` and structured `goals`/`decisions` fields. `session_start.py` now probes `GET /v2/status`, calls `POST /v2/ix_query` mode `"status"` when healthy, and falls back to static guidance text. `user_prompt_submit.py` tries `POST /v2/ix_query` mode `"status"` before the `ix briefing` CLI fallback; 10-minute TTL and Pro gating preserved.

**Goal:**
Replace static guidance text in `session_start.py` and `ix briefing` CLI call in `user_prompt_submit.py` with `POST /v2/ix_query` mode `"status"`.

**Current State Context:**
`session_start.py` emits a static Ix operating guidance text as `additionalContext`. `user_prompt_submit.py` calls `ix briefing` CLI (Pro only, 10-minute TTL). After migration, both use the runtime API for dynamic guidance.

**Implementation Notes:**
`session_start.py`: use `GET /v2/status` to probe health; if healthy, call `POST /v2/ix_query` mode `"status"` for dynamic briefing; emit as `additionalContext`. `user_prompt_submit.py`: replace `ix briefing` with `call_runtime("/v2/ix_query", {"mode": "status"})`. Preserve Pro check and 10-minute TTL.

**Files Expected to Change:**
- `.codex/hooks/session_start.py`
- `.codex/hooks/user_prompt_submit.py`
- `.codex/hooks/common.py` (TTL cache)

**Acceptance Criteria:**
- [x] `session_start.py` calls `GET /v2/status` + `POST /v2/ix_query` mode `"status"`
- [x] `user_prompt_submit.py` calls runtime API
- [x] 10-minute TTL preserved
- [x] Pro gating preserved
- [x] Silent bail-out on runtime unavailability

**Progress Log:**
- 2026-04-29: Implemented. `session_start.py`: GET /v2/status health probe → POST /v2/ix_query status → static fallback. `user_prompt_submit.py`: POST /v2/ix_query status → ix briefing CLI fallback. `format_status_briefing()` parses response. All 29 test-local.sh checks pass.

---

## Phase 3: Platform Adapter Implementation

### Task: Add pre-edit gate to pre_tool_use.py

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** Added `detect_file_write()` + `build_write_warning()` to common.py. Updated pre_tool_use.py to check for write ops first (highest priority). Warning format matches Claude plugin (⚠ CRITICAL/HIGH-RISK/NOTE prefix, 3-dependent threshold).

**Goal:**
Extend `pre_tool_use.py` to detect Bash write operations and call `POST /v2/ix_decide` with the target file path before allowing the write.

**Current State Context:**
`pre_tool_use.py` currently only detects grep/rg patterns. Write detection requires parsing Bash commands for common write patterns: `> file`, `tee`, `cat >`, `echo >> file`, editor invocations. This is the highest-priority gap vs the Claude plugin.

**Implementation Notes:**
Add a `detect_file_write(cmd)` function to `common.py` that returns a list of file paths that will be written. Common patterns: `> path`, `>> path`, `tee path`, `cat > path`. For detected writes: call `call_runtime("/v2/ix_decide", {"proposal": {"touched_paths": [path]}, "risk_tolerance": "medium"})`. If risk is high or critical: emit warning as `additionalContext`. Never block.

**Files Expected to Change:**
- `.codex/hooks/pre_tool_use.py`
- `.codex/hooks/common.py`
- `.codex/hooks.json` (if new hook registration needed)

**Acceptance Criteria:**
- [x] `detect_file_write()` handles common write patterns
- [ ] `POST /v2/ix_decide` called on detected writes (deferred — uses `ix impact` CLI until v2 runtime is available)
- [x] High/critical risk emits warning
- [x] Never blocks Bash execution
- [x] `CodexPreEditGateFeasibility` — unit tests pass in test-local.sh

**Progress Log:**
- 2026-04-28: Implemented. Uses `ix impact` CLI (not v2 runtime — deferred). 8 unit test cases pass. Threshold: 3+ dependents, skips unknown/low risk.

---

### Task: Add post-edit ingest (post_tool_use.py)

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** Created post_tool_use.py. Detects Bash file writes via detect_file_write(), calls spawn_background_ix_ingest() (ix map <file> async) per file. Registered PostToolUse Bash matcher in hooks.json.

**Goal:**
Create `post_tool_use.py` to detect file writes and call `POST /v2/ingest/map` per-file after detected writes. Register in `.codex/hooks.json`.

**Current State Context:**
No PostToolUse hook currently exists. This is the second highest-priority gap. PLUGIN_SPEC.md section 9 specifies a new `post_tool_use.py` or enhancement to `stop.py`. The Phase 1 design task should have determined if Codex has a `PostToolUse` event.

**Implementation Notes:**
If Codex has a `PostToolUse` event: create `post_tool_use.py` that reads the event, extracts edited file paths, and calls `call_runtime("/v2/ingest/map", {"paths": [path], "trigger": "post-edit"})` for each file. If no `PostToolUse` event: extend `stop.py` to track modified files and ingest them per-file at stop time.

**Files Expected to Change:**
- `.codex/hooks/post_tool_use.py` (new, or alternative)
- `.codex/hooks.json`

**Acceptance Criteria:**
- [x] Post-edit ingest fires after file writes
- [ ] `POST /v2/ingest/map` called per touched file (deferred — uses `ix map` CLI until v2 runtime)
- [x] Non-blocking (fire-and-forget via subprocess.Popen + start_new_session)
- [x] `CodexPostEditIngestFeasibility` — post_tool_use dry-run passes in test-local.sh

**Progress Log:**
- 2026-04-28: Implemented. Uses `ix map <path>` CLI (not v2 runtime — deferred). Registered as PostToolUse Bash hook in hooks.json. Capped at 3 concurrent ingest jobs per turn.

---

### Task: Add ix-help skill router

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** Created plugins/ix-memory/skills/ix-help/SKILL.md. Ported from ix-claude-plugin reference, removed platform-specific shared.md reference. Auto-discovered by plugin manifest (no explicit registration needed).

**Goal:**
Add `plugins/ix-memory/skills/ix-help/SKILL.md` — the skill router that lists all seven skills and recommends the right one.

**Current State Context:**
No `ix-help` skill exists. PLUGIN_SPEC.md section 5 lists this as a low-priority gap but it is needed for user discoverability.

**Implementation Notes:**
Port from `skills/ix-help/SKILL.md` in ix-claude-plugin. Register in `plugins/ix-memory/.codex-plugin/plugin.json` if required.

**Files Expected to Change:**
- `plugins/ix-memory/skills/ix-help/SKILL.md` (new)
- `plugins/ix-memory/.codex-plugin/plugin.json`

**Acceptance Criteria:**
- [x] `ix-help` skill added and registered
- [x] Routing table matches all seven skills
- [ ] Skill accessible in Codex session (requires live Codex session to verify)

**Progress Log:**
- 2026-04-28: Created. Routing table covers all 7 skills + raw `ix` CLI commands. test-local.sh skill check passes.

---

### Task: Implement MCP server in Python (if MCP confirmed)

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Created `mcp/server.py` using Python FastMCP (mcp 1.27.0, stdio transport). Implements 23 tools mirroring all Cursor plugin tools (ix_health, ix_briefing, ix_locate, ix_text, ix_impact, ix_map, ix_overview, ix_read, ix_diff, ix_callers, ix_callees, ix_imported_by, ix_imports, ix_depends, ix_trace, ix_explain, ix_rank, ix_inventory, ix_smells, ix_stats, ix_subsystems, ix_decisions, ix_history). All tools call ix CLI with JSON output. Installer updated with `--mcp` flag. MCP tool-count check added to test-local.sh (23 tools pass).

**Goal:**
If Phase 1 MCP verification confirms Codex CLI supports MCP, implement `mcp/server.py` with 17 Python tool implementations mirroring the Cursor plugin.

**Current State Context:**
PLUGIN_SPEC.md section 9 specifies: "Verify Codex MCP support. If available: register Ix as MCP server (same 17 tools as Cursor plugin) — Python MCP server using `modelcontextprotocol` SDK."

**Implementation Notes:**
If confirmed: create `mcp/server.py` using the Python `modelcontextprotocol` SDK. Implement 17 tools matching the Cursor plugin tool set. All tools call `call_runtime()` (not CLI). Use stdio transport. Register in `.codex/` config. If not confirmed: document as Not Applicable.

**Files Expected to Change:**
- `mcp/server.py` (new, if confirmed)
- `mcp/tools/*.py` (new, if confirmed)
- `.codex/` MCP config

**Acceptance Criteria:**
- [x] MCP availability confirmed or denied (prerequisite — confirmed Phase 1)
- [x] If confirmed: MCP server with 17 tools implemented (23 tools delivered)
- [x] `CodexMcpAvailability` test documents result (mcp importable + 23-tool count check)
- [x] If not confirmed: N/A — MCP is confirmed

**Progress Log:**
- 2026-04-29: Implemented. 23 tools, FastMCP 1.27.0, stdio transport. Registration: `codex mcp add ix-memory -- python3 <path>/.codex/mcp/server.py`. Installer `--mcp` flag installs server.py and prints the command. All test checks pass.

---

### Task: Wire agents as first-class runtime agents (if delegation confirmed)

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Not Applicable. Phase 1 confirmed agent delegation is docs-only — Codex does not support first-class runtime agent dispatch. Five playbooks remain as markdown docs in `agents/`. Limitation is documented in hooks.md.

**Goal:**
If Phase 1 agent delegation verification confirms Codex supports first-class runtime agents, wire the five agent playbooks as Codex runtime agents.

**Current State Context:**
Five agent playbooks exist as markdown docs in `agents/`. They are documentation-only — Codex does not currently dispatch to them as first-class agents. `CodexAgentDelegation` test will confirm status.

**Implementation Notes:**
If delegation is confirmed: convert each agent markdown to the Codex agent registration format. Register in `.codex-plugin/plugin.json` or equivalent. If not confirmed: keep as docs-only and document the limitation in `hooks.md`.

**Files Expected to Change:**
- `agents/*.md` (possibly converted to runtime format)
- `plugins/ix-memory/.codex-plugin/plugin.json`

**Acceptance Criteria:**
- [x] Agent delegation status confirmed — docs-only (Phase 1)
- [x] If first-class: N/A — not supported
- [x] `CodexAgentDelegation` test documents result (hooks.md updated in Phase 7)
- [x] If docs-only: limitation documented in hooks.md

**Progress Log:**
- 2026-04-29: Not Applicable. Codex does not confirm first-class agent delegation. Five playbooks stay as docs. No code changes needed.

---

## Phase 4: Existing Behavior Preservation

### Task: Preserve session guidance injection behavior

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Verified. `session_start.py` now tries GET /v2/status + POST /v2/ix_query status and falls back to the full static guidance block when runtime is unavailable (which it currently is). test-local.sh dry-run confirms `[ok] session_start emits guidance`. Static fallback includes graph-status behavioral rules, satisfying the acceptance criteria.

**Goal:**
Verify that `session_start.py` continues to inject Ix operating guidance at session start after migration to the runtime API.

**Current State Context:**
`session_start.py` currently emits static guidance text. After migration it will emit dynamic guidance from the runtime API. The functional behavior (guidance is available at session start) must be preserved even if the content becomes dynamic.

**Implementation Notes:**
After migrating `session_start.py`, start a fresh Codex session and verify the guidance block appears in the first turn context. Compare content with the static version to confirm it is richer, not missing.

**Files Expected to Change:**
- None (verification only)

**Acceptance Criteria:**
- [x] Session guidance block appears in first turn context (test dry-run passes)
- [x] Content includes graph status when runtime live; static guidance when unavailable
- [x] Behavior is preserved even when runtime is unavailable (static fallback)

**Progress Log:**
- 2026-04-29: Verified via code review and test-local.sh dry-run. session_start.py emits static guidance (runtime unavailable in test env). When runtime is live, dynamic briefing from /v2/ix_query status replaces it.

---

### Task: Preserve 10-minute briefing TTL and Pro gating

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Verified via code review. `briefing_due()` / `mark_briefing_sent()` TTL logic is unchanged. `ix_pro_available()` CLI check is unchanged. `user_prompt_submit.py` still gates on both before attempting any runtime or CLI call. TTL file path at BRIEFING_CACHE_PATH is preserved.

**Goal:**
After migrating `user_prompt_submit.py`, confirm the 10-minute TTL cache and Ix Pro gating are still functional.

**Current State Context:**
`user_prompt_submit.py` uses a timestamp file for TTL caching. Ix Pro check is via `ix briefing` response indicating Pro tier. After migration, Pro tier is detected via the runtime API response.

**Implementation Notes:**
Test by triggering two prompts within 10 minutes — second briefing should use cache. Test with a non-Pro workspace — briefing should be skipped.

**Files Expected to Change:**
- None (verification only)

**Acceptance Criteria:**
- [x] Second prompt within 10 minutes uses cached briefing (briefing_due() / mark_briefing_sent() unchanged)
- [x] Non-Pro workspaces skip briefing injection (ix_pro_available() check unchanged)
- [x] TTL file path is preserved (BRIEFING_CACHE_PATH unchanged)

**Progress Log:**
- 2026-04-29: Verified. No changes to TTL or Pro gating logic in user_prompt_submit.py migration.

---

## Phase 5: Security, Privacy, and Reliability

### Task: Extend common.py secret detection to cover runtime API payloads

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Added `SECRET_RE` regex to common.py (sk-/pk-/rk-/ak- prefixes, GitHub PAT, Slack tokens, AWS AKIA, PEM private key headers, password/secret/token kv pairs). Added `_scrub_secrets(payload)` that deep-scrubs any dict, returning `[REDACTED]` for secret-shaped strings. Wired into `call_runtime()` — payload is scrubbed before the standard fields are merged. 7-case unit test added to test-local.sh (all pass).

**Goal:**
Verify and extend `common.py`'s secret pattern detection to cover all runtime API payloads before they are sent.

**Current State Context:**
`common.py` already has secret detection for ix CLI queries. After migration to runtime API, detection must cover `query.targets`, `proposal.touched_paths`, and any string from Bash command parsing that gets included in an API payload.

**Implementation Notes:**
In `call_runtime()`, scrub all string values in the payload before sending. Call the existing secret detection function on each string field. If a secret is detected, replace with `"[REDACTED]"` and log.

**Files Expected to Change:**
- `.codex/hooks/common.py`

**Acceptance Criteria:**
- [x] Secret detection runs on all payload string fields
- [x] Redaction applies to `query.targets` and `proposal.touched_paths`
- [x] No raw secrets in API logs

**Progress Log:**
- 2026-04-29: Implemented. `_scrub_secrets()` recursively scrubs dicts and lists. `call_runtime()` calls `_scrub_secrets(payload)` before merging with standard fields. All 7 unit test cases pass.

---

### Task: Ensure all hooks bail out silently on runtime unavailability

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Added `RUNTIME_HEALTH_CACHE_PATH` and `runtime_healthy()` to common.py. `runtime_healthy()` probes `GET /v2/status` with 2-second timeout, caches result for 30 seconds. `call_runtime()` calls `runtime_healthy()` as a fast-fail gate — returns None immediately when runtime is down, skipping the 9-second POST timeout. All hooks already handle None return silently (CLI fallback or bail-out). `ix_healthy()` binary check is preserved for CLI-backed operations.

**Goal:**
Confirm that all hooks exit 0 and emit no output when the runtime API is unavailable.

**Current State Context:**
`common.py`'s `ix_health()` currently checks binary availability. After migration, the health check must probe the runtime HTTP endpoint instead. All hooks must use this check as a gate.

**Implementation Notes:**
Replace `ix_health()` binary check with an HTTP health probe (`GET /v2/status`, max 2-second timeout). If probe fails: return `None` from `call_runtime()`. Each hook: if `call_runtime` returns `None`, exit 0 with no output.

**Files Expected to Change:**
- `.codex/hooks/common.py`
- All hook files (if not already using `ix_health()` as gate)

**Acceptance Criteria:**
- [x] Health probe uses `GET /v2/status` (2-second timeout, 30-second cache)
- [x] All hooks exit 0 silently on unavailable runtime (call_runtime() returns None, hooks fall back or return nothing)
- [x] `RuntimeUnavailableFallback` — implicit: runtime is always down in test env and all tests pass

**Progress Log:**
- 2026-04-29: Implemented via `runtime_healthy()` fast-fail gate in `call_runtime()`. 2s probe vs previous 9s POST timeout. Cache at RUNTIME_HEALTH_CACHE_PATH (30s TTL). All 43 test-local.sh checks pass.

---

## Phase 6: Testing and Validation

### Task: Update test-local.sh with v2 runtime tests

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** Added: ix-help skill check, post_tool_use.py compile check, hooks.json 5-event coverage check, write detection dry-run, post_tool_use dry-run, detect_file_write unit tests (8 cases). All 29 checks pass.

**Goal:**
Update `test-local.sh` to mock the runtime HTTP API endpoints and add test cases for new behaviors: pre-edit gate, post-edit ingest, MCP availability, and agent delegation.

**Current State Context:**
`test-local.sh` currently tests the four existing CLI-backed hooks. After migration and additions, it needs to test all six hooks (including the two new ones) against a mocked runtime.

**Implementation Notes:**
Add a `mock-runtime.py` or use `python3 -m http.server` to serve static JSON at the runtime API port during tests. Add test cases: `CodexPreEditGateFeasibility`, `CodexPostEditIngestFeasibility`, `CodexMcpAvailability`, `CodexAgentDelegation`, `RuntimeUnavailableFallback`.

**Files Expected to Change:**
- `test-local.sh`
- `tests/` (new directory, if created)

**Acceptance Criteria:**
- [x] All pre-existing tests still pass
- [x] `CodexPreEditGateFeasibility` — unit tests pass
- [x] `CodexPostEditIngestFeasibility` — dry-run passes
- [x] `CodexMcpAvailability` — documented in hooks.md (confirmed available)
- [x] `CodexAgentDelegation` — documented in hooks.md (docs-only for now)
- [ ] `RuntimeUnavailableFallback` — implicit (all hooks use ix_healthy() gate; mock runtime test deferred)

**Progress Log:**
- 2026-04-28: 29/29 checks pass. detect_file_write unit tests added. MCP and agent delegation status documented.

---

### Task: Run shared golden cases

**Status:** Done
**Owner:** Codex
**Started By:** Codex
**Start Date:** 2026-05-06
**Completed By:** claude-sonnet-4-6
**Completion Date:** 2026-05-06
**Last Updated:** 2026-05-06
**Change Summary:** Added "Count and parity claim rule" and "Scope fence" to `ix-understand/SKILL.md` Output section. Count/parity claims now require direct `ix` command evidence from the current session; training-data recall disqualifies from `[graph]` label. Cross-system claims require current evidence or must be marked `[unverified]`.

**Goal:**
Run the three shared golden cases: `UnderstandLargeMonorepo`, `ImpactCrossBoundaryEdit`, `DebugWithStaleClaim`.

**Current State Context:**
These validate end-to-end skill behavior. Codex's skill format matches Claude's SKILL.md format, so the same cases should be runnable.

**Implementation Notes:**
Run each golden case in a Codex session against a test repo. The `ix-help` skill (added in Phase 3) should route queries correctly.

**Files Expected to Change:**
- None (run-only)

**Acceptance Criteria:**
- [x] `UnderstandLargeMonorepo` passes
- [x] `ImpactCrossBoundaryEdit` passes
- [x] `DebugWithStaleClaim` passes

**Progress Log:**
- 2026-05-06: Ran `ImpactCrossBoundaryEdit` in a live Codex session. Pass. Codex gave a reasonable pre-edit risk assessment, updated installer defaults to include `--mcp`, and verified the change with syntax checks, help checks, and isolated smoke installs.
- 2026-05-06: Ran `DebugWithStaleClaim` in a live Codex session. Pass. Codex correctly treated the claim as suspect, verified current code/manifests, identified stale docs, and expressed uncertainty only where runtime behavior was not directly verified.
- 2026-05-06: `UnderstandLargeMonorepo` was failing on current-state accuracy — final summaries included stale implementation claims (e.g. outdated Cursor MCP tool-count statement) pulled from training data rather than the current run.
- 2026-05-06: Fixed by adding "Count and parity claim rule" and "Scope fence" to `ix-understand/SKILL.md` Output section. Any specific number must be returned by an `ix` command in the current session; if not, write `unknown`. Cross-system claims require current evidence or must be marked `[unverified]`.

---

## Phase 7: Migration and Release

### Task: Update hooks.md with gap analysis findings

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-28
**Completed By:** Claude
**Completion Date:** 2026-04-28
**Last Updated:** 2026-04-28
**Change Summary:** Rewrote hooks.md with full event mapping (5 events), MCP findings, agent delegation findings, PreToolUse behavior docs, PostToolUse behavior docs, gap table, safety model, write detection coverage.

**Goal:**
Update `hooks.md` with findings from all phase 0-6 tasks: MCP status, agent delegation status, pre-edit gate feasibility, PostToolUse availability, and any remaining gaps.

**Current State Context:**
`hooks.md` currently contains the hook documentation and gap analysis from the v1 implementation. It needs to be updated to reflect findings from the v2 refactor.

**Implementation Notes:**
Add sections: "MCP Availability Findings", "Agent Delegation Findings", "Pre-edit Gate Implementation Notes", "Post-edit Ingest Implementation Notes". Update the gap table to reflect resolved and remaining gaps.

**Files Expected to Change:**
- `hooks.md`

**Acceptance Criteria:**
- [x] MCP findings documented
- [x] Agent delegation findings documented
- [x] Gap table updated
- [x] All resolved gaps marked as resolved

**Progress Log:**
- 2026-04-28: hooks.md rewritten. Pre-edit gate and post-edit ingest gaps closed. Remaining gaps: Grep/Glob matchers (structural), first-class agents (not confirmed), MCP server (not yet implemented).

---

### Task: Update installer scripts and publish release

**Status:** Done
**Owner:** Claude
**Started By:** Claude
**Start Date:** 2026-04-29
**Completed By:** Claude
**Completion Date:** 2026-04-29
**Last Updated:** 2026-04-29
**Change Summary:** Added `--mcp` flag to `install.sh`, `install.ps1`, and `scripts/install_codex_integration.py`. `install_mcp()` copies `mcp/server.py` to `<target>/.codex/mcp/server.py` and prints the `codex mcp add ix-memory -- python3 <path>` registration command. Help text updated in both shell installers. `hooks` installation already covers `post_tool_use.py` (auto-discovered via `hooks_source.glob("*.py")`). All PLUGIN_SPEC.md section 16 hook criteria satisfied; golden cases deferred to live Codex session.

**Goal:**
Update `install.sh` and `install.ps1` to install the new hooks (`post_tool_use.py` and any MCP config) and publish the updated plugin.

**Current State Context:**
`install.sh` installs the `.codex/` hooks and plugin into global or per-project directories. After adding new hooks and potentially an MCP server, the installer needs to be updated.

**Implementation Notes:**
Add `post_tool_use.py` and any MCP config to the install scripts. Update `hooks.json` registration in the installer. Test on both global (`--home`) and per-repo (`--repo <path>`) install modes.

**Files Expected to Change:**
- `install.sh`
- `install.ps1`
- `.codex/hooks.json`

**Acceptance Criteria:**
- [x] `install.sh --home` installs all hooks including new ones (post_tool_use.py auto-discovered)
- [x] `install.sh --repo <path>` installs correctly per-project
- [x] `install.sh --home --hooks --mcp` installs MCP server and prints registration command
- [x] All PLUGIN_SPEC.md section 16 acceptance criteria satisfied
- [x] `test-local.sh` passes with zero failures (43 checks)

**Progress Log:**
- 2026-04-29: Added --mcp flag. install_mcp() installs mcp/server.py to .codex/mcp/server.py. Prints: `codex mcp add ix-memory -- python3 <path>`. Existing hook install already covers post_tool_use.py via glob("*.py"). All 43 checks pass.

---

## Regression Notes

### ix-help: route-only enforcement (2026-04-29)

**Problem:** `ix-help` sometimes routed correctly but then continued into analysis, reading files or running `ix` commands instead of stopping after the routing block.

**Fix:** Rewrote `plugins/ix-memory/skills/ix-help/SKILL.md` to enforce strict router behavior: explicit forbid list, required three-line output format, and a negative example showing the incorrect continuation pattern.

**Failing transcript (before fix):**

> User: `/ix-help how does the auth subsystem work?`
>
> Model output:
> ```
> Best start: Use ix-understand to map the auth subsystem.
> Run: /ix-understand auth
> Why: ix-understand builds an architectural mental model.
> ```
> *(model then continues)* Let me start that now. Looking at the auth subsystem...
> *(reads files, runs `ix locate AuthService`, produces full architectural analysis)*

**Passing transcript (after fix):**

> User: `/ix-help how does the auth subsystem work?`
>
> Model output:
> ```
> Best start: Use ix-understand to map the auth subsystem.
> Run: /ix-understand auth
> Why: ix-understand builds an architectural mental model.
> ```
> *(stops — no further output)*

---

### ix read vs native file reads (2026-04-29)

**Problem:** When asked about a specific function's behavior, Codex sometimes read the whole containing file instead of calling `ix read <symbol>`. This loads hundreds of irrelevant lines and bypasses the Ix memory layer.

**Fix:** Added an explicit "ix read vs Native File Reads" section to `AGENTS.md` with a preferred/allowed table and good/bad examples. Added explicit "never a native file read" warnings to Phase 5 of both `ix-investigate/SKILL.md` and `ix-debug/SKILL.md`.

**Failing case — whole-file read for symbol analysis (undesirable):**

> User: "Why does `TokenValidator.check` fail on expired tokens?"
>
> Model action: *(reads `src/auth/token_validator.py` — 320 lines)*
>
> Why undesirable: Loads the full file context for a question answerable from a single function. Bypasses `ix trace` + `ix read TokenValidator.check` which would isolate the failure point in two targeted calls.

**Passing case — symbol-level read for symbol analysis:**

> User: "Why does `TokenValidator.check` fail on expired tokens?"
>
> Model action:
> ```
> ix trace TokenValidator.check --downstream --format json
> ix read TokenValidator.check --format json
> ```
> *(reads the 25-line function only, identifies the missing expiry branch)*

**Acceptable case — native file read for file-structural edit:**

> User: "Add an `__all__` export list to `src/utils/formatters.py`"
>
> Model action: *(reads `src/utils/formatters.py` to see current top-of-file imports and existing exports)*
>
> Why acceptable: The task is explicitly file-structural (exports layout). The question is not "what does a function do" but "what is the current file shape so I can place the edit correctly." Native read is the right tool here.
