---
name: ix-tutorial
description: Explain how to use the Ix Memory Codex plugin, including $-prefixed skill invocation, when to use a specific skill, and example prompts for common workflows.
argument-hint: [optional topic or workflow]
---

Do not run `ix` commands, read files, or inspect the repo. This skill is usage guidance only.

## Goal

Explain how to use the Ix Memory plugin inside Codex in the way Codex currently supports:
- `$`-prefixed skill invocation
- plain-language task text after the skill name
- raw `ix` commands only for exact lookups

Be explicit that local Codex plugins may not expose reliable slash-command popups or `/skill` autocomplete. Prefer `$ix-*` prompts the user can paste directly into chat.

## Output

If `$ARGUMENTS` is empty, return a short tutorial with:
- one sentence on what the plugin does
- one sentence saying Codex skills are invoked with `$skill-name`
- one sentence saying users can add plain-language task text after the skill name
- one sentence saying raw `ix` commands are best for exact symbol/file lookups
- 5-7 example prompts

Use this example set, adapted only if the user asked for a narrower topic:
- `$ix-help how does the auth subsystem work?`
- `$ix-understand this repo`
- `$ix-investigate IxClient`
- `$ix-impact Ix/ix-cli/src/client/api.ts`
- `$ix-plan session_start.py common.py`
- `$ix-debug ContextService query flow`
- `Run ix locate IxClient --format json.`

If `$ARGUMENTS` is non-empty, tailor the tutorial to that workflow. Keep it concise:
- answer the user's usage question directly
- recommend the best starting skill or raw command
- give 2-4 copy-paste example prompts

If the user asks about slash commands, say they are not the reliable primary UX for this local plugin setup and `$skill-name` prompts are the recommended path.
