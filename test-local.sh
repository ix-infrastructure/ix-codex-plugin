#!/usr/bin/env bash
# Copyright 2026 Ix Infrastructure Inc.

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

if map_out=$(python3 "$REPO/tests/test_auto_map.py" 2>&1); then
  ok "automatic map only for a mapped git repo, never \$HOME, debounced per root"
else
  fail "auto-map guard tests failed"
  printf '%s\n' "$map_out" | sed 's/^/      /'
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

# Against a fake `ix`, never the real one: these used to run whatever `ix` was on
# PATH, and with a healthy backend the post-write hook then mapped for real --
# into a backend other workspaces share. The fake is strict where the real CLI
# is: `map` of a file and `locate --limit` fail, as they do there. Every call is
# logged, so what the hooks ran is checked rather than assumed.
DRY="$(mktemp -d)"
trap 'rm -rf "$DRY"' EXIT
mkdir -p "$DRY/bin"
cat > "$DRY/bin/ix" << 'FAKEEOF'
#!/bin/sh
echo "$*|$PWD|${IX_AUTO_MAP:-}" >> "$IX_FAKE_LOG"
case "$1" in
  status)
    case " $* " in *" --format json "*) echo '{"backend":"ok","graphCompleted":true}' ;; *) echo "Backend: ok" ;; esac ;;
  map)
    if [ -n "${2:-}" ] && [ "${2#-}" = "$2" ] && [ ! -d "$2" ]; then
      echo "Map path is not a directory: $2" >&2; echo "REJECTED $*" >> "$IX_FAKE_LOG"; exit 1
    fi ;;
  locate)
    case " $* " in *" --limit"*) echo "error: unknown option '--limit'" >&2; echo "REJECTED $*" >> "$IX_FAKE_LOG"; exit 1 ;; esac
    echo '{"resolvedTarget":{"name":"impact","kind":"function","path":"common.py"}}' ;;
  text) echo '{"results":[{"path":"README.md"}]}' ;;
  *) echo '{}' ;;
esac
FAKEEOF
chmod +x "$DRY/bin/ix"
export IX_FAKE_LOG="$DRY/ix.log"
: > "$IX_FAKE_LOG"
dry() { PATH="$DRY/bin:$PATH" XDG_STATE_HOME="$DRY/state" python3 "$REPO/.codex/hooks/$1.py"; }

SESSION_OUT="$(printf '{"cwd":"%s"}' "$REPO" | dry session_start 2>/dev/null || true)"
[ -n "$SESSION_OUT" ] && ok "session_start emits guidance" || fail "session_start produced no output"

PRE_SEARCH="$(printf '{"cwd":"%s","tool_input":{"command":"rg \\"impact\\" README.md"}}' "$REPO" | dry pre_tool_use 2>/dev/null || true)"
[ -n "$PRE_SEARCH" ] && ok "pre_tool_use: search interception executed" || fail "pre_tool_use: no search output"

PRE_PATCH="$(printf '{"cwd":"%s","tool_name":"apply_patch","tool_input":{"command":"*** Begin Patch\\n*** Update File: README.md\\n@@\\n-a\\n+b\\n*** End Patch"}}' "$REPO" | dry pre_tool_use 2>/dev/null || true)"
info "pre_tool_use: apply_patch dry-run completed (output: $([ -n "$PRE_PATCH" ] && echo 'yes' || echo 'none'))"

PRE_WRITE="$(printf '{"cwd":"%s","tool_input":{"command":"echo hello > %s/ix-test-write.py"}}' "$REPO" "$DRY" | dry pre_tool_use 2>/dev/null || true)"
info "pre_tool_use: write detection dry-run completed (output: $([ -n "$PRE_WRITE" ] && echo 'yes' || echo 'none'))"

printf '{"cwd":"%s","tool_input":{"command":"echo hello > %s/ix-test-post.py"}}' "$REPO" "$DRY" | dry post_tool_use >/dev/null 2>&1 \
  && ok "post_tool_use: dry-run completed without error" || fail "post_tool_use: dry-run failed"
STOP_OUT="$(printf '{"cwd":"%s"}' "$REPO" | dry stop 2>/dev/null || true)"
[ "$STOP_OUT" = '{"continue": true}' ] && ok "stop: continues the turn" || fail "stop: unexpected output '$STOP_OUT'"
sleep 1  # the map is detached; let it reach the log

if grep -q '^REJECTED' "$IX_FAKE_LOG"; then
  fail "a hook ran an ix call the real CLI rejects:"; grep '^REJECTED' "$IX_FAKE_LOG" | sed 's/^/      /'
else
  ok "every ix call the hooks made is one the CLI accepts"
fi
# post_tool_use and stop share one debounce window, so exactly one map, of the
# repository root, silent, from the root, marked automatic.
MAPS="$(grep -c '^map ' "$IX_FAKE_LOG" || true)"
if [ "$MAPS" = 1 ] && grep -qx "map $REPO --silent|$REPO|1" "$IX_FAKE_LOG"; then
  ok "one guarded map: ix map <root> --silent, cwd root, IX_AUTO_MAP=1"
else
  fail "expected exactly one guarded root map, log was:"; sed 's/^/      /' "$IX_FAKE_LOG"
fi

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
echo "-- Codex hook protocol checks --"

# Every hook's stdout is validated against the output schemas Codex 0.155.1
# generates, and what each one puts in front of the model is checked: a
# PreToolUse `systemMessage` only ever reached the user.
python3 "$REPO/tests/test_codex_hook_protocol.py" >/dev/null 2>&1 \
  && ok "hook output matches the Codex hook schemas" \
  || fail "Codex hook protocol check failed"

# Every hooks.json command was a `/bin/sh -lc` one-liner, so on native Windows
# no hook could launch at all -- which also kept the PATHEXT fix in common.py
# from ever running (Ix#383). Covers the launcher's search order and the
# installer's Windows-only rewrite, including that it stays a no-op elsewhere.
python3 "$REPO/tests/test_windows_hook_launch.py" >/dev/null 2>&1 \
  && ok "hooks launch without a shell on Windows" \
  || fail "Windows hook-launch check failed"


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
