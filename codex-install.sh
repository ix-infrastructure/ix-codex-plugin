#!/bin/sh
set -eu

GITHUB_ORG="ix-infrastructure"
GITHUB_REPO="ix-codex-plugin"
REPO_URL="https://github.com/${GITHUB_ORG}/${GITHUB_REPO}.git"
REF="${IX_CODEX_REF:-main}"
IX_HOME="${IX_HOME:-$HOME/.ix}"
SOURCE_DIR="${IX_HOME}/codex-plugin-source"

info() { printf "  [ok] %s\n" "$*"; }
warn() { printf "  [!!] %s\n" "$*" >&2; }
err() { printf "  [error] %s\n" "$*" >&2; exit 1; }

show_help() {
  cat <<'EOF'
ix-codex-plugin hosted installer

Usage:
  curl -fsSL https://ix-infra.com/codex-install.sh | sh
  curl -fsSL https://ix-infra.com/codex-install.sh | sh -s -- --mcp
  curl -fsSL https://ix-infra.com/codex-install.sh | sh -s -- --repo /path/to/project --plugin --hooks

Behavior:
  - Clones or updates ix-codex-plugin into ~/.ix/codex-plugin-source
  - Runs scripts/install_codex_integration.py from that checkout
  - Defaults to: --home --plugin --hooks

Options:
  All remaining arguments are forwarded to install_codex_integration.py.

Environment:
  IX_CODEX_REF   Branch or tag to install from (default: main)
  IX_HOME        Base directory for the cached source checkout (default: ~/.ix)
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "$1 is required but was not found."
  fi
}

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  err "Python 3 is required to run the Codex installer."
}

repo_is_dirty() {
  [ -n "$(git -C "$SOURCE_DIR" status --short 2>/dev/null || true)" ]
}

sync_repo() {
  if [ -d "$SOURCE_DIR/.git" ]; then
    if repo_is_dirty; then
      warn "Using existing checkout without updating because it has local changes: $SOURCE_DIR"
      return
    fi
    info "Updating cached source checkout in $SOURCE_DIR"
    git -C "$SOURCE_DIR" remote set-url origin "$REPO_URL"
    git -C "$SOURCE_DIR" fetch --depth 1 origin "$REF"
    git -C "$SOURCE_DIR" checkout --quiet FETCH_HEAD
    return
  fi

  if [ -e "$SOURCE_DIR" ]; then
    err "$SOURCE_DIR exists but is not a git checkout."
  fi

  mkdir -p "$IX_HOME"
  info "Cloning ix-codex-plugin into $SOURCE_DIR"
  git clone --depth 1 --branch "$REF" "$REPO_URL" "$SOURCE_DIR"
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  show_help
  exit 0
fi

require_cmd git
PYTHON_BIN="$(find_python)"
sync_repo

has_target=0
has_action=0
for arg in "$@"; do
  case "$arg" in
    --home|--repo)
      has_target=1
      ;;
    --plugin|--hooks|--mcp)
      has_action=1
      ;;
  esac
done

if [ "$has_target" -eq 0 ]; then
  set -- --home "$@"
fi
if [ "$has_action" -eq 0 ]; then
  set -- --plugin --hooks "$@"
fi

exec "$PYTHON_BIN" "$SOURCE_DIR/scripts/install_codex_integration.py" "$@"
