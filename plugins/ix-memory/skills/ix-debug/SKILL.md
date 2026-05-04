---
name: ix-debug
description: Root cause analysis — trace execution path to a failure, narrow candidates, read minimal source only at suspected failure points.
argument-hint: <symptom, failing function, or suspected component>
---

Check `command -v ix` first. If unavailable, use Grep + Read as fallback.

## Graph freshness (always check before Phase 1)

Read the session briefing context. If it reports `stale: true`:
- Label every graph-backed claim in your output as `[stale graph]`.
- Set confidence ceiling to `low` regardless of how clean individual symbol lookups appear.
- Note in the output: "Graph freshness: STALE — confidence is capped at low."

Do not skip this check. A fresh-looking symbol response does not mean the graph is current.

## Goal

Answer: where in the execution path is this likely failing, and why? Stop once you have 1-3 root cause candidates with supporting evidence.

## Phase 1 — Locate the entry point (always)

```bash
ix locate $ARGUMENTS --format json
```

If `$ARGUMENTS` is a symptom description rather than a symbol name, also run:
```bash
ix text "$ARGUMENTS" --limit 10 --format json
```

Identify the most likely entry point (where the failure originates or first manifests).

**Qualified member lookup rule:** If you attempt `ix locate <Class.method>` and it fails to resolve, stay scoped to the parent class — do NOT retry with just the method name as a bare symbol. Bare member names resolve to unrelated entities and produce noisy, misleading evidence.

## Phase 2 — Explain (always)

```bash
ix explain <entry-point> --format json
```

Extract: role, callers, callees, confidence. Identify whether this is:
- A boundary (external input, API, event) — failure likely from unexpected input
- An orchestrator — failure likely from wrong sequencing or state
- A utility/helper — failure likely from wrong assumptions by caller

Stop if: the explanation makes the failure source obvious -> skip to Output.

## Phase 3 — Trace the execution path

```bash
ix trace <entry-point> --downstream --format json
```

Walk the downstream path. At each step, look for:
- Functions that validate or transform state (potential incorrect assumptions)
- Cross-subsystem calls (where contracts might differ)
- Functions with high callee count (potential god functions, many failure points)

Narrow: Identify the 1-3 nodes most likely to contain the bug.

Stop if: trace reveals an obvious candidate -> proceed to Phase 5.

## Phase 4 — Callers (if failure might come from upstream)

```bash
ix callers <entry-point> --limit 10 --format json
```

Check whether the fault is in how this is called rather than in its own logic.

## Phase 5 — Targeted code read (only at suspected failure points)

For each root cause candidate (max 2):
```bash
ix read <candidate-function> --format json
```

Use `ix read <candidate-function>` — **never a native read of the whole file**.

**If `ix read <symbol>` returns ambiguous or unresolved:**
1. Try the resolved file path from `ix locate`: `ix read <path-from-locate> --format json`
2. If that also fails — STOP. Do not fall back to a whole-file native read. Reduce confidence, note which symbol could not be confirmed, and surface the ambiguity in the output.

**Whole-file read ceiling:** At most one whole-file read is permitted per run, and only when absolutely no symbol-level path succeeded. Once a whole-file read has occurred, do NOT issue additional whole-file reads for downstream collaborators or related files. Synthesize from available graph + source evidence and reduce confidence instead.

Look for:
- Edge cases in input handling
- Assumptions about state that might be violated
- Missing null/error checks
- Incorrect sequencing

Hard limit: 2 source reads maximum (symbol-level or path-level). Whole-file reads count against this limit. If still ambiguous after the limit, surface the candidates and uncertainty to the user.

## Output

```text
## Debug: [entry point]

**Execution path:**
[entry-point] -> [step] -> [step] -> [suspected failure point]

**Root cause candidates:**
1. [function/file] — [reason: what assumption might be wrong]
2. [function/file] — [reason]

**Evidence:**
- [what graph data supports each candidate]
- [what code read revealed, if any]

**Confidence:** [high / medium / low] — [why; if graph was stale or any read was whole-file, explain the penalty]

**Next steps:**
- Add logging at [specific point] to confirm
- Check [specific edge case] in [function]
- Run `ix-investigate <X>` to understand [unclear component] more deeply
```
