---
name: ix-plan
description: Generate a risk-ordered implementation plan for a set of targets. Assesses blast radius per target, finds data flows between them, and produces a safe change sequence.
argument-hint: <symbol1> [symbol2] [symbol3] ... OR description of change
---

Check `command -v ix` first. If unavailable, use Grep + Read to manually assess blast radius per target.

## Goal

Answer: in what order should these changes be made, what will break, and what needs testing?

## Phase 1 — Scope (always)

If `$ARGUMENTS` contains symbol names, proceed.
If `$ARGUMENTS` is a description (no identifiable symbols), first run:
```bash
ix text "$ARGUMENTS" --limit 10 --format json
ix locate "$ARGUMENTS" --format json
```
Identify the 1-4 most relevant symbols and treat those as targets.

## Phase 2 — Impact per target (parallel)

For each identified target, run simultaneously:
```bash
ix impact  <target> --format json
ix callers <target> --limit 10 --format json
```

Rank targets by risk level: critical > high > medium > low.

**Text-fallback confidence rule:** If `ix callers` or `ix depends` for any target returns a text fallback (i.e. results are not graph-backed — no symbol IDs, only prose or filename strings), that target's impact assessment must be marked **lower confidence** in the Output with the reason explicitly stated (e.g. "ix callers fell back to text — graph coverage partial for this target").

**Fast path — all low risk:** If every target is `low` risk AND has < 3 dependents, skip Phases 3 and 4. Go directly to Output with verdict "SAFE — all targets low risk; no data-flow or shared-dependent analysis needed."

## Phase 3 — Data flow (only if 2+ targets AND at least one is medium/high/critical)

Find how the targets connect:
```bash
ix trace <highest-risk-target> --to <second-target> --format json
```

Run for the most architecturally significant pair. Skip if targets are in independent subsystems.

## Phase 4 — Shared dependents (only if high/critical targets exist; skip if all low risk)

```bash
ix depends <highest-risk-target> --depth 2 --format json
```

Identify if any third symbol depends on multiple targets (shared blast radius — highest testing priority).

**Shared-blast-radius qualification rule:** Any claim that "no shared downstream symbol was found across these targets" (or equivalent) must explicitly state which target pairs were actually checked with `ix depends` and which were not. If `ix depends` was run for only one target, the claim is only valid for that target's dependents. Never generalize across all targets unless every pair was verified.

## Phase 5 — Ix Pro plan (if ix pro available)

If `ix briefing` or session context indicates active plans exist, you **must** run:
```bash
ix plans --format json
```
Use the output to qualify change ordering — existing plans may already cover some targets or establish priority constraints. If active plans are present and this step is skipped, the plan output is incomplete.

Skip this phase only if ix pro is confirmed unavailable.

## Output

```text
# Change Plan

## Targets & Risk

| Target | Risk | Dependents | Key Callers |
|--------|------|------------|-------------|
| <A>    | high | 12         | X, Y, Z     |
| <B>    | low  | 2          | P           |

## Change Order

Edit in this sequence to minimize breakage:
1. [target] — [reason: lowest risk / most-depended-upon first]
2. ...

## Data Flow
[A -> trace path -> B — or "targets are independent"]

## Shared Risk
[Symbols affected by changes to multiple targets — these need testing after every change]

## Test Checkpoints
After [target A]: verify [specific callers]
After [target B]: verify [specific callers]

## Red Flags
- [any critical/high target needing extra care]
- [any cross-subsystem boundary being crossed]
```

Do not read source code in this skill unless a target cannot be resolved by `ix locate`.
