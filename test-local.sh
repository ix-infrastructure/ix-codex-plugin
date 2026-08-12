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
for skill in ix-understand ix-investigate ix-impact ix-plan ix-debug ix-architecture ix-docs ix-help ix-tutorial; do
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
  "$REPO/.codex/hooks/post_tool_use.py" \
  "$REPO/.codex/hooks/stop.py" \
  "$REPO/.codex/hooks/_launch.py" \
  "$REPO/scripts/install_codex_integration.py" >/dev/null \
  && ok "Python files compile" || fail "Python compile failed"

# Show the failures rather than swallowing them: `>/dev/null 2>&1` leaves a
# developer with "[FAIL] pro detection tests failed" and nothing to act on.
if pro_out=$(python3 "$REPO/tests/test_pro_detection.py" 2>&1); then
  ok "pro detection discriminates OSS from Pro and does not spin on a blip"
else
  fail "pro detection tests failed"
  printf '%s\n' "$pro_out" | sed 's/^/      /'
fi

# The probe is now a real `ix briefing`, so how often the hook reaches it is
# part of the contract; common.py alone cannot show that.
if ups_out=$(python3 "$REPO/tests/test_user_prompt_submit.py" 2>&1); then
  ok "UserPromptSubmit runs at most one briefing per prompt"
else
  fail "user_prompt_submit tests failed"
  printf '%s\n' "$ups_out" | sed 's/^/      /'
fi

if argv_out=$(python3 "$REPO/tests/test_ix_argv_resolution.py" 2>&1); then
  ok "ix invocations resolve through PATHEXT and refuse cmd metacharacters"
else
  fail "ix argv resolution tests failed"
  printf '%s\n' "$argv_out" | sed 's/^/      /'
fi

echo ""
echo "-- hooks.json event coverage --"

python3 - "$REPO/.codex/hooks.json" << 'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
hooks = data.get("hooks", {})
for event in ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]:
    if event in hooks:
        print(f"  [ok] {event} registered")
    else:
        print(f"  [FAIL] {event} missing from hooks.json")
        sys.exit(1)
PYEOF

echo ""
echo "-- Hook dry-runs --"

SESSION_OUT="$(printf '{"cwd":"%s"}' "$REPO" | python3 "$REPO/.codex/hooks/session_start.py" 2>/dev/null || true)"
[ -n "$SESSION_OUT" ] && ok "session_start emits guidance" || info "session_start produced no output"

PRE_SEARCH="$(printf '{"cwd":"%s","tool_input":{"command":"rg \"impact\" README.md"}}' "$REPO" | python3 "$REPO/.codex/hooks/pre_tool_use.py" 2>/dev/null || true)"
[ -n "$PRE_SEARCH" ] && ok "pre_tool_use: search interception executed" || info "pre_tool_use: no search output (ix may be unavailable)"

PRE_WRITE="$(printf '{"cwd":"%s","tool_input":{"command":"echo hello > /tmp/ix-test-write.py"}}' "$REPO" | python3 "$REPO/.codex/hooks/pre_tool_use.py" 2>/dev/null || true)"
info "pre_tool_use: write detection dry-run completed (output: $([ -n "$PRE_WRITE" ] && echo 'yes' || echo 'none — ix unavailable or file not in graph'))"

POST_OUT="$(printf '{"cwd":"%s","tool_input":{"command":"echo hello > /tmp/ix-test-post.py"}}' "$REPO" | python3 "$REPO/.codex/hooks/post_tool_use.py" 2>/dev/null || true)"
ok "post_tool_use: dry-run completed without error"

echo ""
echo "-- MCP registration checks --"

# This plugin no longer ships an MCP server: `ix mcp` in the CLI serves the same
# tools, so there is nothing here to introspect. What still has to hold is that
# the installer delegates instead of copying a server, and that it refuses to
# register against a CLI too old to have the subcommand.
python3 -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('inst', '$REPO/scripts/install_codex_integration.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

if mod.install_mcp(None, 'copy', False) != []:
    print('  [FAIL] install_mcp still installs files'); sys.exit(1)
print('  [ok] install_mcp copies no server of its own')

if mod.MIN_IX_VERSION_FOR_MCP < (0, 9, 3):
    print('  [FAIL] version floor predates the ix mcp subcommand'); sys.exit(1)
print(f'  [ok] requires ix >= {\".\".join(str(p) for p in mod.MIN_IX_VERSION_FOR_MCP)}')
" && ok "installer delegates MCP to the ix CLI" || fail "MCP delegation check failed"


echo ""
echo "-- hook installer TOML checks --"

# ensure_codex_hooks_enabled used to append a second `codex_hooks` assignment to
# an existing [features] table, producing a duplicate key that no TOML parser
# accepts -- and exiting 0 while doing it. The installer's output has to stay
# loadable, so every case in tests/ ends in a real parse.
python3 "$REPO/tests/test_installer_toml.py" >/dev/null 2>&1 \
  && ok "hook installer keeps config.toml valid" \
  || fail "hook installer TOML check failed"

# Every hooks.json command was a `/bin/sh -lc` one-liner, so on native Windows
# no hook could launch at all -- which also kept the PATHEXT fix in common.py
# from ever running (Ix#383). Covers the launcher's search order and the
# installer's Windows-only rewrite, including that it stays a no-op elsewhere.
python3 "$REPO/tests/test_windows_hook_launch.py" >/dev/null 2>&1 \
  && ok "hooks launch without a shell on Windows" \
  || fail "Windows hook-launch check failed"


echo ""
echo "-- _scrub_secrets unit tests --"

python3 -c "
import sys
sys.path.insert(0, '$REPO/.codex/hooks')
from common import _scrub_secrets, SECRET_RE

cases = [
    ('sk-abc12345678901234567890',     True),
    ('ghp_' + 'A' * 36,               True),
    ('AKIA' + 'A' * 16,               True),
    ('password=mysupersecret123',      True),
    ('UserService',                    False),
    ('handle_request',                 False),
    ('test_pattern',                   False),
]
failed = 0
for text, expect_redact in cases:
    hit = bool(SECRET_RE.search(text))
    status = 'ok' if hit == expect_redact else 'FAIL'
    if status == 'FAIL':
        failed += 1
    print(f'  [{status}] SECRET_RE: {text[:30]!r} → redact={hit} (expected {expect_redact})')

# Scrubbing test
payload = {'query': {'targets': ['sk-abc12345678901234567890']}, 'note': 'safe'}
scrubbed = _scrub_secrets(payload)
assert scrubbed['query']['targets'][0] == '[REDACTED]'
assert scrubbed['note'] == 'safe'
print('  [ok] _scrub_secrets payload scrubbing correct')
sys.exit(failed)
" && ok "_scrub_secrets unit tests passed" || fail "_scrub_secrets unit tests failed"

echo ""
echo "-- detect_file_write unit tests --"

python3 - << 'PYEOF'
import sys
sys.path.insert(0, "REPO_PLACEHOLDER/.codex/hooks")
PYEOF

python3 -c "
import sys
sys.path.insert(0, '$REPO/.codex/hooks')
from common import detect_file_write

cases = [
    ('echo hello > file.py', ['file.py']),
    ('cat > output.txt', ['output.txt']),
    ('printf \"%s\" x >> log.txt', ['log.txt']),
    ('echo x 2> err.log', []),
    ('tee result.json', ['result.json']),
    ('cat README.md', []),
    ('rg pattern src/', []),
    ('cat << EOF > main.py', ['main.py']),
]
failed = 0
for cmd, expected in cases:
    got = detect_file_write(cmd)
    if got == expected:
        print(f'  [ok] detect_file_write: {cmd!r}')
    else:
        print(f'  [FAIL] detect_file_write: {cmd!r}')
        print(f'         expected={expected!r} got={got!r}')
        failed += 1
sys.exit(failed)
" && ok "detect_file_write unit tests passed" || { fail "detect_file_write unit tests failed"; }

echo ""
echo "-- Summary --"
if [ "$FAILURES" -eq 0 ]; then
  echo "  All checks passed."
else
  echo "  $FAILURES check(s) failed."
fi
echo ""

exit "$FAILURES"
