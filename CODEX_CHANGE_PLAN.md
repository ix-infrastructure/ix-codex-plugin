# Codex Plugin — Change Plan

Derived from: `CODEX_MANUAL_TEST_LOG.md` (baseline date 2026-05-03)

---

## How to Use This File

Each task has a **Change Record** block at the bottom. When a task is completed, fill in:
- `Date` — ISO date of the change (YYYY-MM-DD)
- `Author` — model or human who made the change (e.g. `claude-sonnet-4-6`, `ianhock`)
- `Summary` — one or two sentences describing what was done and why
- `Files Changed` — list of paths modified, created, or deleted

Mark the task status as one of: `[ ] todo` → `[~] in-progress` → `[x] done`

---

## Phase 1: Plugin & Installer

> Goal: Make the installed plugin version visible to users and ensure the marketplace entry is complete.

### Task 1.1 — Expose active version in plugin UI `[x] done`

**Problem:** The only way to see the installed version is to read `~/.codex/ix-plugin-version.json`. There is no surface in the plugin UI that shows this.

**Expected outcome:** The active version string (and ideally the installed commit) is visible somewhere in the Codex UI or a user-facing command response without needing to open the version file manually.

**Test:** Ask the assistant or the plugin for the current version; it should return the version without the user pointing it at `ix-plugin-version.json`.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | `session_start.py` already injects the version into `additionalContext`; updated `ix-help` SKILL.md so the empty-args menu surfaces the version line from session context as the first output line. |
| Files Changed | `plugins/ix-memory/skills/ix-help/SKILL.md` |

---

### Task 1.2 — Write a fuller marketplace entry on install `[x] done`

**Problem:** `~/.agents/plugins/marketplace.json` is missing `version` and `description` fields for the ix plugin. These are populated at install time.

**Expected outcome:** `install_codex_integration.py` (or whichever installer script handles this) writes a marketplace entry that includes at minimum `version`, `description`, and ideally `source_path`.

**Test:** Re-run the installer and confirm the marketplace entry contains all required fields.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added `_read_plugin_json()` helper shared by `write_version_file` and `install_plugin`; `update_marketplace` now accepts `version` and `description` params and includes them in the written entry; `install_plugin` reads these from `plugin.json` and passes them through. |
| Files Changed | `scripts/install_codex_integration.py` |

---

## Phase 2: PreToolUse Hook

> Goal: Stop the PreToolUse hook from emitting incorrect or mismatched SKILL.md warnings.

### Task 2.1 — Fix noisy / mismatched SKILL.md warnings in PreToolUse `[x] done`

**Problem:** The `PreToolUse` hook emits `SKILL.md` warnings that appear to reference the wrong skill (e.g. headings from `ix-plan` appear during an `ix-architecture` run). This was observed across multiple skill runs and is consistent enough to treat as a structural bug.

**Expected outcome:** PreToolUse either emits no warning, or emits a warning that correctly names the skill currently being opened. Headings in the warning should match the active skill file.

**Test:** Run each of the eight skills in sequence and check that no warning emits a heading from a different skill.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Two fixes in `common.py`: (1) added `"skill.md"` to `READ_SKIP_BASENAMES` so SKILL.md reads are never intercepted by the hook; (2) `build_read_message` now computes the path relative to the workspace root for `ix overview` and `ix impact` calls instead of using just the basename, eliminating same-name ambiguity for all files. |
| Files Changed | `.codex/hooks/common.py` |

---

## Phase 3: ix-debug

> Goal: Fix the four known behavior regressions in the ix-debug skill.

### Task 3.1 — Keep disambiguated reads symbol-level; block whole-file fallback `[x] done`

**Problem:** When `ix read <symbol>` comes back ambiguous, `ix-debug` falls back to a whole-file read instead of stopping or reducing confidence. This was observed twice in the same run (once for `ContextService`, once for `GraphSeeder`).

**Expected outcome:** After an ambiguous symbol read, the skill either (a) attempts a more specific qualified lookup and stops if that also fails, or (b) reduces confidence and proceeds graph-only. It does not fall back to a whole-file `ix read <path>`.

**Test:** Trigger a debug run on a target with an ambiguous `ix read` response and confirm no whole-file read follows.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Rewrote Phase 5 of `ix-debug/SKILL.md`: if `ix read <symbol>` is ambiguous, try the located file path next; if that also fails, STOP and reduce confidence rather than falling back to a whole-file native read. |
| Files Changed | `plugins/ix-memory/skills/ix-debug/SKILL.md` |

---

### Task 3.2 — Block broadening from failed qualified member to bare symbol `[x] done`

**Problem:** When `ix locate ContextService.query` fails to resolve, `ix-debug` broadened to `ix locate query` and `ix explain query`, which resolved to unrelated CLI code and added noisy evidence rather than narrowing.

**Expected outcome:** If a qualified member lookup fails (e.g. `ContextService.query`), the skill should stay scoped to the parent class or stop. It must not retry with just the member name as a bare symbol.

**Test:** Trigger a debug run where the target method cannot be resolved; confirm the skill does not issue a bare-symbol fallback for the method name alone.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added explicit "Qualified member lookup rule" to Phase 1 of `ix-debug/SKILL.md`: if `ix locate <Class.method>` fails, stay scoped to the parent class and do NOT retry with the bare method name. |
| Files Changed | `plugins/ix-memory/skills/ix-debug/SKILL.md` |

---

### Task 3.3 — Reduce confidence when session briefing reports stale graph `[x] done`

**Problem:** `ix-debug` acknowledged method-level ambiguity in its output but did not explicitly downgrade confidence for stale graph freshness even though `ix briefing` reported `stale: true` with an age of ~70 minutes.

**Expected outcome:** If `ix briefing` reports `stale: true`, the skill must include an explicit confidence penalty in its output (e.g. "Graph freshness: stale — confidence reduced to low regardless of symbol resolution quality").

**Test:** Trigger a debug run when the graph is stale and confirm the output contains an explicit stale-graph confidence reduction.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added "Graph freshness" section at the top of `ix-debug/SKILL.md` (before Phase 1): if session briefing reports `stale: true`, label all graph claims `[stale graph]` and cap confidence at `low`. Also updated the Output confidence line to require explaining any stale-graph or whole-file penalties. |
| Files Changed | `plugins/ix-memory/skills/ix-debug/SKILL.md` |

---

### Task 3.4 — Block chaining into additional whole-file reads of downstream collaborators `[x] done`

**Problem:** After the first `ContextService.scala` whole-file read was forced, `ix-debug` also read `GraphSeeder.scala` in full. Once a run has already escaped symbol-level mode, it should not continue reading additional collaborator files.

**Expected outcome:** Once the skill has been forced out of symbol-level mode (i.e. fell back to at least one whole-file read), it must not issue additional whole-file reads for downstream collaborators. It should synthesize from what it has and reduce confidence instead.

**Test:** Run a debug session that requires a whole-file fallback and confirm only one such file is read total.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added "Whole-file read ceiling" rule to Phase 5 of `ix-debug/SKILL.md`: at most one whole-file read per run; once it occurs, no further whole-file reads for collaborators — synthesize from available evidence and reduce confidence instead. |
| Files Changed | `plugins/ix-memory/skills/ix-debug/SKILL.md` |

---

## Phase 4: ix-investigate

> Goal: Fix the class → member → ix read disambiguation path so it does not dead-end.

### Task 4.1 — Reliable class → member → ix read disambiguation path `[x] done`

**Problem:** `ix read GraphSeeder` returns `ambiguous-file` and `ix locate GraphSeeder.seed` fails even though `GraphSeeder.seed` is a known member in the graph. The skill has no fallback path and cannot answer symbol-level questions in this case.

**Expected outcome:** When `ix read <class>` is ambiguous, the skill should (a) use the resolved file path from `ix locate` to call `ix read <path>` at the symbol level, or (b) if that also fails, note which members are unresolved and reduce confidence rather than silently stopping. The disambiguation chain should be documented in the skill instructions.

**Test:** Run an investigate session on a class that has a known ambiguous-file `ix read` response and confirm the skill uses the located path or reduces confidence explicitly.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added "Disambiguation chain for ambiguous-file responses" to Phase 5 of `ix-investigate/SKILL.md`: when `ix read <class>` returns ambiguous-file, use the resolved path from `ix locate`; if that also fails, note unresolved members and reduce confidence to `uncertain` rather than falling back to a whole-file native read. |
| Files Changed | `plugins/ix-memory/skills/ix-investigate/SKILL.md` |

---

## Phase 5: ix-understand

> Goal: Remove the invalid bare `ix inventory` command usage from skill examples.

### Task 5.1 — Remove or fix bare `ix inventory` usage in skill instructions `[x] done`

**Problem:** `ix-understand` attempted `ix inventory --format json` without the required `--kind` flag and hit a command error. This suggests the skill instructions or examples include a bare `ix inventory` invocation.

**Expected outcome:** All `ix inventory` examples in skill instructions include the required `--kind` option. Any instruction that implies `ix inventory` can be called without `--kind` is corrected.

**Test:** Run a whole-repo understand pass and confirm no invalid `ix inventory` command is attempted.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | The bare `ix inventory` usage was not in any SKILL.md but was being inferred by the LLM. Added an explicit "Never" rule to `AGENTS.md`: never run `ix inventory` without `--kind` (required). |
| Files Changed | `AGENTS.md` |

---

## Phase 6: ix-plan

> Goal: Fix three known accuracy and confidence issues in the ix-plan skill.

### Task 6.1 — Check for existing plans when plan context is active `[x] done`

**Problem:** `ix-plan` did not run `ix plans --format json` despite an active plan context being present in the session. The skill either needs to always run this check or explicitly say why it was skipped.

**Expected outcome:** If `ix briefing` or session context indicates active plans exist, `ix-plan` runs `ix plans --format json` early in its run and uses that output to qualify its change ordering.

**Test:** Run ix-plan in a session with an existing plan; confirm `ix plans --format json` appears in the tool call log.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Strengthened Phase 5 of `ix-plan/SKILL.md`: when session context indicates active plans exist, `ix plans --format json` is now **mandatory** (not optional); skip only if ix pro is confirmed unavailable. |
| Files Changed | `plugins/ix-memory/skills/ix-plan/SKILL.md` |

---

### Task 6.2 — Qualify shared-blast-radius claims unless all intersections were checked `[x] done`

**Problem:** `ix-plan` stated "No shared downstream symbol was found across these three targets" even though `ix depends` was not run for all three targets and tracing was only done outward from `IxClient`. This is stronger than the evidence supports.

**Expected outcome:** Shared-blast-radius claims must be qualified with which target pairs were actually checked and which were not. If the full intersection has not been verified, the output must say so.

**Test:** Run ix-plan on three targets and confirm any shared-blast claim lists exactly which pairs were verified.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added "Shared-blast-radius qualification rule" to Phase 4 of `ix-plan/SKILL.md`: any no-shared-dependent claim must list exactly which target pairs were verified with `ix depends`; generalization across all targets is disallowed unless every pair was checked. |
| Files Changed | `plugins/ix-memory/skills/ix-plan/SKILL.md` |

---

### Task 6.3 — Downgrade confidence when file/script targets force `ix callers` to text fallback `[x] done`

**Problem:** When `ix callers` on `install_codex_integration.py` degraded to a text fallback (no graph-backed results), `ix-plan` did not visibly downgrade confidence for that target's impact assessment.

**Expected outcome:** If `ix callers` or `ix depends` returns a text fallback rather than graph-backed results for any target, the plan for that target must be marked lower confidence and the reason stated explicitly.

**Test:** Run ix-plan on a target where `ix callers` returns a text fallback and confirm the plan explicitly notes the fallback and reduces confidence for that target.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added "Text-fallback confidence rule" to Phase 2 of `ix-plan/SKILL.md`: if `ix callers` or `ix depends` returns text-only (non-graph-backed) results for any target, that target's impact assessment must be explicitly marked lower confidence with the reason stated. |
| Files Changed | `plugins/ix-memory/skills/ix-plan/SKILL.md` |

---

## Phase 7: ix-architecture

> Goal: Fix ambiguous subsystem targeting and improve the region-selection strategy.

### Task 7.1 — Handle ambiguous subsystem names cleanly `[x] done`

**Problem:** Several targeted `ix subsystems` lookups returned ambiguous or `unknown_target` results. The skill spent substantial effort resolving these ad hoc instead of using a clean region-selection strategy.

**Expected outcome:** The skill should not attempt raw region IDs with `ix subsystems <id>` if the CLI does not support that form. After ambiguous results, it should fall back to a clearly documented recovery path (e.g. filtering from the full `ix subsystems --list` output).

**Test:** Run ix-architecture on a repo with multiple subsystems and confirm no `unknown_target` errors appear, or that any error is immediately handled by the documented fallback.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added "Unknown-target recovery" rule to Phase 1 of `ix-architecture/SKILL.md`: if a scoped `ix subsystems` call returns `unknown_target` or ambiguous results, immediately fall back to filtering the full `--list` output; never retry with variant spellings or raw numeric IDs. |
| Files Changed | `plugins/ix-memory/skills/ix-architecture/SKILL.md` |

---

### Task 7.2 — Use explicit region-selection from `ix subsystems --list` before ad hoc lookups `[x] done`

**Problem:** The skill issued many ad hoc scoped `ix subsystems` lookups before exhausting a structured top-down approach. `ix subsystems --list` provides the canonical set of region IDs and should be the starting point.

**Expected outcome:** The skill instructions specify that `ix subsystems --list --format json` must be run first to enumerate valid region IDs, and that subsequent targeted lookups use only IDs from that enumeration.

**Test:** Run ix-architecture and confirm that all subsequent scoped `ix subsystems` calls use IDs that appeared in the initial `--list` output.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Established `--list` output as canonical region ID enumeration in Phase 1 of `ix-architecture/SKILL.md`: all subsequent scoped `ix subsystems` calls must use only IDs from that list; argument resolution against the list is required before any targeted lookup. |
| Files Changed | `plugins/ix-memory/skills/ix-architecture/SKILL.md` |

---

## Phase 8: ix-docs

> Goal: Fix three known accuracy issues in the ix-docs skill.

### Task 8.1 — Add repo-scoping fallback when `ix locate <repo-name>` fails `[x] done`

**Problem:** `ix locate ix-codex-plugin` and `ix overview ix-codex-plugin` both failed. The skill had no fallback and wandered into repo-global top-ranked files (including unrelated non-plugin files) before re-scoping.

**Expected outcome:** The skill instructions describe a fallback strategy for when repo-name targeting fails: use `ix locate` with an exact path prefix (e.g. `ix locate ix-codex-plugin/`) or `ix subsystems --list` to find the matching region, before issuing any global ranking commands.

**Test:** Run ix-docs on a plugin subdirectory and confirm it does not produce results from unrelated repo-global top-ranked files.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added "Repo-scoping fallback" block to Phase 1 of `ix-docs/SKILL.md`: when `ix locate "$TARGET"` fails, try path-prefix form (`"$TARGET/"`), then `ix subsystems --list` to find a matching region; if none match, stop and ask the user rather than falling through to repo-global ranking. |
| Files Changed | `plugins/ix-memory/skills/ix-docs/SKILL.md` |

---

### Task 8.2 — Validate file-path references in generated docs `[x] done`

**Problem:** The final doc written by ix-docs used the path `/home/ianhock/ix/mcp/server.py` instead of the correct plugin-local path `ix-codex-plugin/mcp/server.py`. Multiple same-name files exist elsewhere in the monorepo.

**Expected outcome:** The skill instructions require that any file path written into the output document is verified against the `ix locate` results that were used during the run, not inferred from memory or global context.

**Test:** Run ix-docs and inspect the output doc for any path that was not resolved during the run; flag any that reference non-plugin paths.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added "File-path reference rule" to Phase 7 of `ix-docs/SKILL.md`: any path written into the output doc must have been returned by a graph command in the current run; inference from memory or target name is disallowed, and same-named files elsewhere in the monorepo must not be substituted. |
| Files Changed | `plugins/ix-memory/skills/ix-docs/SKILL.md` |

---

### Task 8.3 — Fix the symbol handoff from `ix explain` / `ix locate` into `ix read` `[x] done`

**Problem:** Two `ix read` calls at the end of the ix-docs run used unresolved symbol IDs and produced no evidence. The skill needs a more reliable way to carry the resolved ID from `ix explain` or `ix locate` through to `ix read`.

**Expected outcome:** The skill instructions describe how to pass the symbol reference from `ix explain`/`ix locate` into `ix read`. If the ID returned by `ix locate` cannot be used directly by `ix read`, the instructions must describe the correct form to use (e.g. qualified name, path-based reference).

**Test:** Run ix-docs on a target with resolvable symbols and confirm all `ix read` calls use IDs that were returned by a prior `ix locate` or `ix explain` call in the same run.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Added "Symbol handoff rule" to Phase 7 of `ix-docs/SKILL.md`: `ix read` must use the exact ID from a prior `ix locate`/`ix explain` call; if that ID doesn't resolve, try fully-qualified or path-based form; if neither works, skip and note the gap rather than using an unverified ID. |
| Files Changed | `plugins/ix-memory/skills/ix-docs/SKILL.md` |

---

## Phase 9: Command & Flag Examples

> Goal: Remove or correct references to unsupported flags across all skill instruction files.

### Task 9.1 — Audit and fix unsupported flag examples across skill docs `[x] done`

**Problem:** Several skill instruction files contain examples with flags that are not supported by the corresponding `ix` subcommand. Known instances:
- `ix callers --path` — `--path` is not a valid flag for `ix callers`
- `ix inventory` without `--kind` — `--kind` is required
- `ix locate --limit` — `--limit` is not a valid flag for `ix locate`

**Expected outcome:** All command examples in skill instruction files use only flags that are documented and valid for that subcommand. A one-time audit of every `ix` example across all skill files is performed and corrected.

**Test:** Run each skill and confirm no tool call attempts a flag that returns a CLI error. A secondary test is to grep all skill files for flag patterns and cross-reference against the ix CLI help output.

---
**Change Record**

| Field | Value |
|---|---|
| Date | 2026-05-03 |
| Author | claude-sonnet-4-6 |
| Summary | Audited all SKILL.md files and AGENTS.md — no bad flag examples were present in the current files (they had already been cleaned up). The LLM was inferring the unsupported flags. Added three explicit "Never" rules to `AGENTS.md` to prevent inference: no `ix callers --path`, no `ix callees --path`, no `ix locate --limit`. |
| Files Changed | `AGENTS.md` |

---

## Summary Table

| Phase | Task | Status |
|---|---|---|
| 1 — Plugin & Installer | 1.1 Expose version in plugin UI | `[x] done` |
| 1 — Plugin & Installer | 1.2 Fuller marketplace entry on install | `[x] done` |
| 2 — PreToolUse Hook | 2.1 Fix mismatched SKILL.md warnings | `[x] done` |
| 3 — ix-debug | 3.1 Block whole-file fallback after ambiguous read | `[x] done` |
| 3 — ix-debug | 3.2 Block broadening to bare symbol after failed qualified lookup | `[x] done` |
| 3 — ix-debug | 3.3 Reduce confidence on stale graph | `[x] done` |
| 3 — ix-debug | 3.4 Block chaining additional whole-file reads | `[x] done` |
| 4 — ix-investigate | 4.1 Reliable class → member → ix read path | `[x] done` |
| 5 — ix-understand | 5.1 Fix bare `ix inventory` usage | `[x] done` |
| 6 — ix-plan | 6.1 Check existing plans when plan context is active | `[x] done` |
| 6 — ix-plan | 6.2 Qualify shared-blast-radius claims | `[x] done` |
| 6 — ix-plan | 6.3 Downgrade confidence on text-fallback callers | `[x] done` |
| 7 — ix-architecture | 7.1 Handle ambiguous subsystem names cleanly | `[x] done` |
| 7 — ix-architecture | 7.2 Use `ix subsystems --list` before ad hoc lookups | `[x] done` |
| 8 — ix-docs | 8.1 Repo-scoping fallback when locate fails | `[x] done` |
| 8 — ix-docs | 8.2 Validate file-path references in output | `[x] done` |
| 8 — ix-docs | 8.3 Fix symbol handoff into `ix read` | `[x] done` |
| 9 — Command & Flag Examples | 9.1 Audit and fix unsupported flag examples | `[x] done` |
