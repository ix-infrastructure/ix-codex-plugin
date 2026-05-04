# Codex Manual Test Log

Date: 2026-05-03
Repo: `/home/ianhock/ix/ix-codex-plugin`
Purpose: Track live Codex skill/hook validation, observed regressions, and likely fixes.

## Current Summary

- `ix-help`: pass after stricter routing-only prompt
- `ix-understand`: pass after recent skill/instruction fixes
- `ix-investigate`: pass
- `ix-impact`: pass
- `ix-debug`: pass with caveats
- `ix-plan`: pass with caveats
- `ix-architecture`: pass with caveats
- `ix-docs`: pass with caveats

## Version / Install Notes

- Active installed plugin metadata file exists at `~/.codex/ix-plugin-version.json`
- Installed plugin version observed: `2.3.0`
- Installed source path observed: `/home/ianhock/ix/ix-codex-plugin`
- Installed git commit observed: `e77dd914e040a4d4a6c801ac4dbba079647e653c`
- Important: this git commit is the local repo `HEAD`, not a GitHub push indicator

## Tests Run

### 1. `ix-help` routing-only

Prompt intent:
- Route a high-level monorepo understanding request without executing it

Initial behavior:
- Failed once by routing correctly and then continuing into actual analysis

Retest behavior:
- Passed with stricter prompt: returned only `Best start`, `Run`, `Why`
- Returned exact invocation: `ix-understand .`

Assessment:
- Current state looks good
- Regression risk remains because the failure was previously reproducible

### 2. `ix-understand` whole-repo

Prompt intent:
- Understand the repo at a high level using graph-first behavior

Earlier issues:
- Tried to run `ix-understand .` as if it were a shell command
- Tried invalid `ix locate . --limit 5 --format json`
- Later also used invalid `ix locate <symbol> --limit 10 --format json`

Retest behavior after changes:
- Treated this as a skill invocation, not a shell command
- Explicitly skipped `ix locate .`
- Did not use `ix locate --limit`
- Produced a graph-first repo overview with subsystem and centrality analysis

Assessment:
- Pass

### 3. `ix-investigate IxClient`

Prompt intent:
- Investigate `IxClient` with graph-first analysis and symbol-level read only if needed

Observed behavior:
- Used `ix locate IxClient`
- Used `ix explain IxClient`
- Used `ix callers IxClient --limit 15 --format json`
- Used `ix trace IxClient --downstream --format json`
- Used `ix read IxClient --format json`
- Did not read the whole `api.ts` file

Assessment:
- Pass
- Strong evidence that symbol-level read preference can work correctly

### 4. `ix-impact` on `Ix/ix-cli/src/client/api.ts`

Prompt intent:
- Measure blast radius before editing a high-risk boundary file

Observed behavior:
- Used `ix impact`
- Expanded with `ix imported-by`, `ix callers`, `ix depends`
- Stayed graph-only
- Classified target as `critical` and `boundary`
- Recommended narrowing impact analysis to the specific `IxClient` method before editing

Assessment:
- Pass

### 5. `ix-debug` on `ContextService`

Prompt intent:
- Debug the query flow around `ContextService` with graph-first analysis and symbol-level reads only if needed

Observed behavior:
- Used `ix locate ContextService --format json`
- Used `ix explain ContextService --format json`
- Used `ix callers ContextService --limit 10 --format json`
- Used `ix trace ContextService --downstream --format json`
- Narrowed to `query` within `ContextService.scala`
- Hit one invalid flag attempt: `ix callers query ... --path ...` where `ix callers` did not accept `--path`
- Attempted exact-symbol `ix read` by ID and failed to resolve
- Fell back to `ix read ContextService`, then to `ix read ix-memory-layer/src/main/scala/ix/memory/context/ContextService.scala --format json`

Assessment:
- Pass with caveats
- Good: graph-first narrowing, limited candidate set, explicit medium confidence, no overclaiming
- Not ideal: read fallback eventually became a file-level read, which is weaker than the intended symbol-level-only behavior
- Not ideal: one more unsupported flag mismatch surfaced (`ix callers --path`)

### 6. `ix-plan` on `IxClient`, `ContextService`, `install_codex_integration.py`

Prompt intent:
- Produce a graph-first, risk-ordered change plan across three targets
- Identify per-target risk, likely affected callers/dependents, data-flow connections, shared blast radius, and test checkpoints

Observed behavior:
- Used `ix locate` for all three targets and resolved them without reading source
- Used `ix impact` and `ix callers` per target
- Used `ix depends` for `IxClient` and `install_codex_integration.py`
- Used `ix trace IxClient --to ContextService`
- Used `ix trace IxClient --to ix-codex-plugin/scripts/install_codex_integration.py`
- Produced a sensible edit order: `ContextService` -> `install_codex_integration.py` -> `IxClient`
- Gave concrete post-edit test checkpoints per target

Assessment:
- Pass with caveats
- Good: stayed graph-first and source-free, ranked targets plausibly by risk, and produced concrete test checkpoints
- Not ideal: `PreToolUse` again emitted noisy `SKILL.md` warnings with mismatched headings
- Not ideal: did not appear to run `ix plans --format json` or otherwise check existing Ix Pro plans despite active plan context being present
- Not ideal: "No shared downstream symbol was found across these three targets" is stronger than the evidence shown because `ix depends` was not run for all three targets and tracing was only done outward from `IxClient`
- Not ideal: installer-script caller data was weaker than symbol-backed results because `ix callers` degraded to text fallback for `install_codex_integration.py`

### 7. `ix-architecture` on `ix-codex-plugin`

Prompt intent:
- Produce a graph-only structural audit of the repo
- Identify major regions, weak boundaries, smells, hotspots, improvement areas, and healthy areas with explicit graph evidence

Observed behavior:
- Used `ix subsystems --list --format json`
- Used `ix subsystems --format json`
- Used `ix smells --format json`
- Used `ix rank --by dependents --kind class --top 10 --exclude-path test --format json`
- Used `ix rank --by dependents --kind function --top 10 --exclude-path test --format json`
- Stayed source-free
- Used additional graph commands (`ix locate`, `ix impact`, targeted `ix subsystems`) to anchor hotspot files and regions
- Produced a concrete structural summary covering CLI, API, context, tools, hooks, and DB hotspots

Assessment:
- Pass with caveats
- Good: stayed graph-only, surfaced concrete hotspot files/symbols, and explicitly called out low-confidence boundaries
- Good: did not overreact to the large smell count and instead weighted non-orphan structural findings more heavily
- Not ideal: `PreToolUse` again emitted a noisy `SKILL.md` warning, and the headings shown appeared to come from `ix-plan` rather than `ix-architecture`
- Not ideal: region targeting was somewhat brittle; several targeted `ix subsystems` lookups were ambiguous, and direct ID lookups returned `unknown_target`
- Not ideal: the skill spent substantial effort resolving ambiguous region names ad hoc instead of using a cleaner region-selection strategy up front

### 8. `ix-docs` on `ix-codex-plugin`

Prompt intent:
- Produce narrative-first onboarding documentation for the plugin with a selective reference layer
- Stay graph-first, keep reads rare, and use symbol-level `ix read` only if critical behavior remains unclear

Observed behavior:
- Read the skill and explicitly chose standard mode / single-document output
- Used `ix stats`, `ix subsystems --list`, `ix subsystems --format json`, and attempted `ix locate` / `ix overview` on `ix-codex-plugin`
- Switched away from repo-name targeting after the graph did not resolve `ix-codex-plugin` as a first-class entity
- Initially followed repo-global rank results into non-plugin files (`ix-ingest.ts`, `ix-map.ts`, `ix-impact.ts`, `ix-docs-tool.ts`, `ix-neighbors.ts`, `ix-query.ts`) before correcting back to plugin-native files
- Narrowed to plugin-local installer and MCP files using exact-path `ix locate`, `ix explain`, `ix impact`, `ix contains`, and `ix callers`
- Attempted two symbol-ID `ix read` calls at the end; both failed to resolve and no native whole-file read was used
- Wrote a narrative doc to `ix-codex-plugin.md`

Assessment:
- Pass with caveats
- Good: produced an actual narrative doc, stayed largely graph-first, and kept the read budget low
- Good: did not fall back to native full-file reads after the symbol-ID `ix read` failures
- Not ideal: `PreToolUse` emitted the same noisy `SKILL.md` warning twice in the same run
- Not ideal: repo targeting was weak at the start; `ix locate ix-codex-plugin` and `ix overview ix-codex-plugin` failed, and the skill spent time recovering from that
- Not ideal: repo-global ranking caused a false lead into unrelated non-plugin files before the scope was re-narrowed
- Not ideal: the final doc used at least one incorrect path reference (`/home/ianhock/ix/mcp/server.py` instead of the plugin-local `ix-codex-plugin/mcp/server.py`)
- Not ideal: the two `ix read` attempts used unresolved symbol IDs and produced no extra evidence, suggesting the skill needs a more reliable handoff from `ix explain`/`ix locate` into `ix read`

### 9. `ix-debug` retest on `ContextService`

Prompt intent:
- Retest `ix-debug` under stricter rules: graph first, symbol-level `ix read` only if needed, no whole-file reads unless absolutely necessary, and explicit confidence reduction if graph evidence is stale or ambiguous

Observed behavior:
- Used `ix locate ContextService --format json`
- Used `ix explain ContextService --format json`
- Used `ix trace ContextService --downstream --format json`
- Tried `ix locate ContextService.query --format json` and failed to resolve it
- Used `ix callers ContextService --limit 10 --format json`
- Used `ix explain ContextRoutes --format json`
- Used `ix trace ContextRoutes --downstream --format json`
- Used unqualified `ix callers query --limit 10 --format json` and `ix explain query --format json`, which resolved to unrelated CLI `query` behavior rather than `ContextService.query`
- Fell back to `ix read ContextService --format json`, which returned `ambiguous-file`
- Then fell back to `ix read ix-memory-layer/src/main/scala/ix/memory/context/ContextService.scala --format json`
- Also read another full file with `ix read ix-memory-layer/src/main/scala/ix/memory/context/GraphSeeder.scala --format json`
- Produced explicit `Graph-Backed Facts`, `Source-Backed Facts`, and `Inferences`
- Reported `Confidence: medium`
- Did note method-level ambiguity, but did not appear to account for the session briefing freshness state (`stale: true`, age about 70 minutes)

Assessment:
- Pass with caveats
- Good: followed graph-first narrowing initially, separated facts from inferences, kept the candidate list tight, and surfaced a plausible concrete bug around `asOfRev`
- Not ideal: despite the stricter prompt, the skill still fell back to whole-file reads, and it did so twice rather than once
- Not ideal: after `ContextService.query` failed to resolve, the skill broadened to bare `query`, which resolved to unrelated CLI code and added noisy evidence rather than narrowing safely
- Not ideal: confidence reduction was only partial; the run acknowledged method ambiguity but did not clearly downgrade for stale graph freshness even though the session briefing marked the graph stale
- Not ideal: the extra `GraphSeeder.scala` whole-file read suggests the skill still lacks a reliable symbol-level handoff path when the first `ix read` target is ambiguous

### 10. `ix-investigate` on `GraphSeeder`

Prompt intent:
- Investigate `GraphSeeder` as the likely source of `asOfRev` drift under strict source discipline
- Use graph first, allow exactly one `ix read <symbol>` call, and do not read whole files

Observed behavior:
- Checked `ix briefing --format json` and `ix status --format json` up front and correctly observed a fresh graph (`stale: false`)
- Used `ix callers GraphSeeder --limit 10 --format json`
- Used `ix callees GraphSeeder --limit 10 --format json`
- Used `ix locate GraphSeeder --format json`
- Used `ix explain GraphSeeder --format json`
- Attempted exactly one source read with `ix read GraphSeeder --format json`
- The single `ix read` returned `ambiguous-file`
- Did not retry with a second read and did not fall back to whole-file reads
- Used `ix explain ContextService --format json`
- Tried `ix locate GraphSeeder.seed --format json` and failed to resolve it
- Used `ix explain GraphQueryApi --format json`

Assessment:
- Pass with caveats
- Good: respected the one-read budget, avoided whole-file fallback, stayed tightly scoped, and reduced confidence when source confirmation failed
- Good: handled graph freshness correctly when the briefing reported `stale: false`
- Not ideal: symbol-level handoff is still weak; `ix read GraphSeeder` degraded to `ambiguous-file` rather than yielding symbol source
- Not ideal: member lookup `ix locate GraphSeeder.seed` failed even though the graph reported `seed` as a member of `GraphSeeder`
- Not ideal: the skill could not actually answer the core `asOfRev` question from graph evidence alone, so the disambiguation path from class -> member -> `ix read` still needs work

### 11. `ix-understand` freshness check on `ix-codex-plugin/mcp/server.py`

Prompt intent:
- Assess whether the graph is fresh enough to reason safely about `ix-codex-plugin/mcp/server.py`
- Stay graph-only, remain file-scoped, and explicitly stop if graph confidence is not high enough for edits

Observed behavior:
- Checked `ix briefing --format json` and correctly observed a fresh graph (`stale: false`)
- Tried `ix inventory --format json` and hit a command error because `--kind` was missing
- Used `ix overview ix-codex-plugin/mcp --format json`
- Used `ix overview ix-codex-plugin/mcp/server.py --format json`
- Used `ix explain mcp/server.py --format json`
- Used `ix rank --by dependents --kind function --path mcp/server.py --top 10 --format json`
- Used `ix rank --by callers --kind function --path mcp/server.py --top 10 --format json`
- Did not read source
- Did not wander into repo-global ranking or unrelated files
- Concluded the graph was sufficient for orientation but not sufficient to proceed safely with edits on graph evidence alone

Assessment:
- Pass with caveats
- Good: obeyed graph-only discipline, checked freshness up front, stayed scoped to the target file/region, and refused to overclaim edit safety from incomplete graph coverage
- Not ideal: still emitted an invalid command attempt with `ix inventory --format json` despite the subcommand requiring `--kind`
- Not ideal: confidence could likely have been downgraded more aggressively given unresolved callees plus zero resolved callers/dependents on the target file, although the final recommendation to stop before editing was correct

### 12. `ix-investigate` on `IxClient` with strict source discipline

Prompt intent:
- Re-run `ix-investigate` on `IxClient` with strict rules against native file reads, unrelated symbol broadening, and unnecessary source reads

Observed behavior:
- Used `ix callers IxClient --limit 10 --format json`
- Used `ix callees IxClient --limit 10 --format json`
- Used `ix locate IxClient --format json`
- Used `ix explain IxClient --format json`
- Used `ix trace IxClient --downstream --format json`
- Did not use any source read
- Did not use any native file read
- Did not broaden into unrelated similarly named symbols

Assessment:
- Pass
- Good: clean symbol resolution, tight scope, no source read regression, no native file read regression, and no ambiguous-symbol drift
- Good: produced a plausible caller -> `IxClient.query` -> `post` path and highlighted shared transport methods as the likely risk area
- Minor caveat: class-level `ix callees IxClient` returned no edges while downstream trace still showed meaningful member fan-in, which suggests graph presentation inconsistency, but the skill handled that inconsistency reasonably

## Hook / Runtime Behavior Observed

### Good

- `SessionStart` hook is firing
- `UserPromptSubmit` briefing hook is firing
- Pre-edit warning fired before editing `Ix/ix-cli/src/client/api.ts`
- Installed version metadata file is being written
- Updated session guidance now explicitly says skills should be invoked as Codex skills, not shell commands

### Problems Still Open

- `PreToolUse` emits noisy `SKILL.md` warnings with mismatched headings that do not appear to correspond to the actual skill being read
- Plugin marketplace entry in `~/.agents/plugins/marketplace.json` is still reduced and omits `version` and `description`
- Briefing output is verbose; acceptable for now, but may be worth trimming later if it crowds out work

## Behavior / Instruction Issues Seen

### Fixed or improved

- `ix-help` now behaves correctly with a strict routing-only prompt
- `ix-understand` no longer appears to run as a shell command in the latest retest
- Whole-repo `ix-understand` behavior now skips `ix locate .`
- Invalid `ix locate --limit` behavior no longer appeared in the latest `ix-understand` retest
- `ix-investigate` on `GraphSeeder` respected the exact one-read budget and did not fall back to whole-file reads after the symbol read came back ambiguous
- Freshness handling improved when the graph was current; recent `ix-investigate` / `ix-understand` runs surfaced the briefing state and used it in confidence framing
- `ix-understand` on `mcp/server.py` stayed file-scoped and did not wander into repo-global ranking when target resolution was weaker
- `ix-investigate` on `IxClient` completed cleanly with zero source reads and no unrelated symbol drift

### Still worth fixing

- The plugin should make the active version more obvious in the plugin UI, not only through `~/.codex/ix-plugin-version.json`
- Installer should likely write a fuller marketplace entry including `version` and `description`
- `PreToolUse` should avoid generic or incorrect `SKILL.md` warnings when opening skill files
- `ix-debug` / surrounding instructions may need adjustment so disambiguated symbol reads stay symbol-level and avoid falling back to whole-file reads when exact symbol resolution fails
- `ix-debug` should avoid broadening from failed qualified member lookup to unrelated bare-symbol lookup, e.g. `ContextService.query` -> `query`
- `ix-debug` should reduce confidence more explicitly when the session briefing reports stale graph freshness, even if individual symbol responses look fresh
- `ix-debug` should avoid chaining into additional whole-file reads of downstream collaborators once the prompt has already been forced out of symbol-level mode
- `ix-investigate` should have a more reliable disambiguation path from class -> member -> `ix read`, so `ix read GraphSeeder` and `ix locate GraphSeeder.seed` do not dead-end on ambiguous-file / unresolved-member outcomes
- `ix-understand` / related command examples should avoid invalid bare `ix inventory` usage and supply required options such as `--kind`
- `ix-plan` should either run `ix plans --format json` when plan context exists or explicitly say why that phase was skipped
- `ix-plan` should qualify shared-blast-radius claims unless all relevant target intersections were actually checked
- `ix-plan` should downgrade confidence more explicitly when file/script targets force `ix callers` into text fallback
- `ix-architecture` should handle ambiguous subsystem names more cleanly and avoid trying raw region IDs with `ix subsystems` if the CLI does not support that form
- `ix-architecture` should prefer an explicit region-selection strategy from `ix subsystems --list` before issuing many ad hoc scoped lookups
- `ix-docs` should have a better repo-scoping fallback when `ix locate <repo-name>` and `ix overview <repo-name>` fail, so it does not wander into repo-global top-ranked files
- `ix-docs` should validate file-path references in generated docs, especially when multiple same-name files exist elsewhere in the monorepo
- `ix-docs` should use a more reliable symbol handoff into `ix read`; unresolved symbol-ID reads added latency but no evidence
- Some Ix command examples in skills still appear to assume unsupported flags on certain subcommands, e.g. `ix callers --path`

## Edit-Behavior Observation

Test:
- Asked Codex to add a temporary comment at the top of `Ix/ix-cli/src/client/api.ts`

Observed behavior:
- Pre-edit impact warning fired correctly
- Codex then read `api.ts` directly instead of using `ix read`

Interpretation:
- This is not necessarily a failure
- For a file-structural edit like a top-of-file comment, native file read can be acceptable
- For analysis tasks, native whole-file reads should still be discouraged in favor of `ix read <symbol>`

## Remaining Suggested Tests

Current recommendation:

- Start fixes now. The manual test baseline is broad enough that new runs are mostly refining known issue classes rather than discovering new ones.
- Reserve further prompts for post-fix regression checks, especially around stale-graph confidence handling and symbol-level read disambiguation.
