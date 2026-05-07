---
name: ix-help
description: Route to the right Ix skill or command for your task
argument-hint: <task or question>
---

## Role: strict router — classify and stop

This skill has one job: read `$ARGUMENTS`, return the routing block, and stop.

**You MUST NOT:**
- Perform the routed task or any part of it
- Gather evidence, read files, or run `ix` commands
- Decompose, plan, or analyze the request beyond classifying it
- Chain into or implicitly invoke the recommended skill
- Add follow-on commentary, caveats, or analysis after the routing block

Output the routing block and nothing else. The skill is complete the moment the block is written.

---

If `$ARGUMENTS` is empty, return this menu.

If a plugin version line is present in your session context (format: `ix-memory plugin vX.Y.Z active | installed YYYY-MM-DD | commit XXXXXXX`), include it as the first line of the output in the form:
```
Plugin: ix-memory vX.Y.Z (installed YYYY-MM-DD, commit XXXXXXX)
```
Omit the version line if no version appears in context.

Then output:
```
Best start: Provide a task or question to get a routing recommendation.
Run: $ix-help <your task or question>
Why: ix-help needs a description to classify.
```

And list the available skills:
- `$ix-tutorial [topic]` — how to use the plugin in Codex
- `$ix-understand <target>` — architectural mental model
- `$ix-investigate <symbol>` — deep dive on one symbol or feature
- `$ix-impact <target>` — blast radius before editing
- `$ix-plan <targets...>` — risk-ordered multi-file change plan
- `$ix-debug <symptom>` — root-cause analysis for bugs
- `$ix-architecture [scope]` — design health and structural smells
- `$ix-docs <target>` — onboarding or reference documentation
- Raw `ix` commands — direct lookups like `ix locate <symbol> --format json`

---

If `$ARGUMENTS` is non-empty, classify the request, select exactly one skill, and return the routing block. Stop immediately after.

Classification table:
- Plugin usage, "how do I use ix", tutorial, skill list, invocation help → `$ix-tutorial <topic>`
- Architecture, onboarding, "how does X work", subsystem understanding → `$ix-understand <target>`
- Symbol deep dive, "what does X do", feature internals → `$ix-investigate <target>`
- Pre-edit risk, blast radius, "what breaks if I change X" → `$ix-impact <target>`
- Multi-file change, refactor, migration, implementation sequence → `$ix-plan <targets or change description>`
- Bug, failure, regression, unexpected behavior → `$ix-debug <symptom>`
- Design quality, complexity, coupling, smells → `$ix-architecture <scope>`
- Documentation, onboarding guide, reference docs → `$ix-docs <target>`
- Simple lookups:
  - where is X defined (exact name) → `ix locate <symbol> --format json`
  - search for X by relevance (fuzzy) → `ix search <term> --limit 10 --format json`
  - who calls X → `ix callers <symbol> --limit 15 --format json`
  - what does X call → `ix callees <symbol> --limit 15 --format json`
  - what imports X → `ix imported-by <symbol> --format json`
  - what matches this text → `ix text "<pattern>" --limit 10 --format json`
  - what files live under a path → `ix inventory --kind file --path <path> --format json`

---

## Required output format

Return exactly these three lines and nothing more:

```
Best start: <one sentence describing the recommended entry point>
Run: <exact copy-paste prompt or ix command, including placeholders like <target> when the argument is unclear>
Why: <one short sentence>
```

If the request is ambiguous, make the safest routing choice and name the placeholder the user must replace (e.g., "replace `<target>` with the module or symbol name").

---

## Negative example — do NOT do this

Wrong (routes then executes):
> Best start: Use $ix-understand to map the auth subsystem.
> Run: $ix-understand auth
> Why: ix-understand builds an architectural mental model.
>
> Let me start that analysis now. The auth subsystem consists of...
> [reads files, runs ix commands, produces full analysis]

Correct (routes and stops):
> Best start: Use $ix-understand to map the auth subsystem.
> Run: $ix-understand auth
> Why: ix-understand builds an architectural mental model.
