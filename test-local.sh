#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAILURES=0

ok() { echo "  [ok] $*"; }
fail() { echo "  [FAIL] $*"; FAILURES=$((FAILURES + 1)); }
info() { echo "  ---  $*"; }

echo ""
echo "========================================="
echo "  ix-codex-plugin - local validation"
echo "========================================="
echo ""

echo "-- Checking structure --"

[ -d "$REPO/plugins/ix-memory" ] && ok "plugin tree found" || fail "missing plugins/ix-memory"
[ -f "$REPO/plugins/ix-memory/.codex-plugin/plugin.json" ] && ok "plugin manifest found" || fail "missing plugin.json"
[ -f "$REPO/.agents/plugins/marketplace.json" ] && ok "marketplace found" || fail "missing marketplace.json"
[ -f "$REPO/.codex/hooks.json" ] && ok "hooks.json found" || fail "missing hooks.json"
[ -f "$REPO/AGENTS.md" ] && ok "AGENTS.md found" || fail "missing AGENTS.md"
[ -f "$REPO/hooks.md" ] && ok "hooks.md found" || fail "missing hooks.md"

echo ""
echo "  Skills:"
for skill in ix-understand ix-investigate ix-impact ix-plan ix-debug ix-architecture ix-docs; do
  [ -f "$REPO/plugins/ix-memory/skills/$skill/SKILL.md" ] && ok "$skill" || fail "missing $skill"
done

echo ""
echo "  Removed helper skills:"
for skill in ix-search ix-explain ix-trace ix-smells ix-depends ix-subsystems ix-diff ix-read ix-before-edit; do
  [ ! -f "$REPO/plugins/ix-memory/skills/$skill/SKILL.md" ] && ok "removed $skill" || fail "stale skill present: $skill"
done

echo ""
echo "  Agent playbooks:"
for agent in ix-explorer ix-system-explorer ix-bug-investigator ix-safe-refactor-planner ix-architecture-auditor; do
  [ -f "$REPO/agents/$agent.md" ] && ok "$agent" || fail "missing agents/$agent.md"
done

echo ""
echo "-- Validating JSON and Python --"

python3 -c 'import json, pathlib; json.load(open(pathlib.Path("'"$REPO"'") / "plugins/ix-memory/.codex-plugin/plugin.json")); print("ok")' >/dev/null \
  && ok "plugin.json parses" || fail "plugin.json invalid"
python3 -c 'import json, pathlib; json.load(open(pathlib.Path("'"$REPO"'") / ".agents/plugins/marketplace.json")); print("ok")' >/dev/null \
  && ok "marketplace.json parses" || fail "marketplace.json invalid"
python3 -c 'import json, pathlib; json.load(open(pathlib.Path("'"$REPO"'") / ".codex/hooks.json")); print("ok")' >/dev/null \
  && ok "hooks.json parses" || fail "hooks.json invalid"
python3 -m py_compile \
  "$REPO/.codex/hooks/common.py" \
  "$REPO/.codex/hooks/session_start.py" \
  "$REPO/.codex/hooks/user_prompt_submit.py" \
  "$REPO/.codex/hooks/pre_tool_use.py" \
  "$REPO/.codex/hooks/stop.py" \
  "$REPO/scripts/install_codex_integration.py" >/dev/null \
  && ok "Python files compile" || fail "Python compile failed"

echo ""
echo "-- Hook dry-runs --"

SESSION_OUT="$(printf '{"cwd":"%s"}' "$REPO" | python3 "$REPO/.codex/hooks/session_start.py" 2>/dev/null || true)"
[ -n "$SESSION_OUT" ] && ok "session_start emits guidance" || info "session_start produced no output"

PRE_OUT="$(printf '{"cwd":"%s","tool_input":{"command":"rg \"impact\" README.md"}}' "$REPO" | python3 "$REPO/.codex/hooks/pre_tool_use.py" 2>/dev/null || true)"
[ -n "$PRE_OUT" ] && ok "pre_tool_use executed" || info "pre_tool_use produced no output"

echo ""
echo "-- Summary --"
if [ "$FAILURES" -eq 0 ]; then
  echo "  All checks passed."
else
  echo "  $FAILURES check(s) failed."
fi
echo ""

exit "$FAILURES"
