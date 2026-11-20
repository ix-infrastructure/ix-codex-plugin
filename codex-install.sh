#!/bin/sh
# Copyright 2026 Ix Infrastructure Inc.

set -eu

GITHUB_ORG="ix-infrastructure"
GITHUB_REPO="ix-codex-plugin"
REPO_URL="https://github.com/${GITHUB_ORG}/${GITHUB_REPO}.git"
REF="${IX_CODEX_REF:-main}"
REF_FROM_ENV=0
if [ -n "${IX_CODEX_REF:-}" ]; then
  REF_FROM_ENV=1
fi
IX_HOME="${IX_HOME:-$HOME/.ix}"
SOURCE_DIR="${IX_HOME}/codex-plugin-source"

info() { printf "  [ok] %s\n" "$*"; }
warn() { printf "  [!!] %s\n" "$*" >&2; }
err() { printf "  [error] %s\n" "$*" >&2; exit 1; }

show_help() {
  cat <<'EOF'
ix-codex-plugin hosted installer

Usage:
  curl -fsSL https://raw.githubusercontent.com/ix-infrastructure/ix-codex-plugin/main/codex-install.sh | sh
  curl -fsSL https://raw.githubusercontent.com/ix-infrastructure/ix-codex-plugin/main/codex-install.sh | sh -s -- --mcp
  curl -fsSL https://raw.githubusercontent.com/ix-infrastructure/ix-codex-plugin/main/codex-install.sh | sh -s -- --repo /path/to/project --plugin --hooks
  curl -fsSL https://raw.githubusercontent.com/ix-infrastructure/ix-codex-plugin/<tag>/codex-install.sh | sh -s -- --ref <tag>

Behavior:
  - Fetches the requested ref of ix-codex-plugin into ~/.ix/codex-plugin-source
  - Runs scripts/install_codex_integration.py from that checkout
  - Defaults to: --home --plugin --hooks --mcp

Options:
  --ref <ref>    Branch, tag or full commit SHA to install (overrides IX_CODEX_REF)
  All remaining arguments are forwarded to install_codex_integration.py.

Environment:
  IX_CODEX_REF   Branch, tag or full commit SHA to install from (default: main)
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

# The ref ends up as an argument to `git fetch`, so it must not be read as an
# option there, and must name one ref rather than a `src:dst` refspec.
check_ref() {
  case "$REF" in
    ""|-*|*:*|*[[:space:]]*)
      err "Not a branch, tag or commit: '$REF'."
      ;;
  esac
}

# Fetch $REF into the existing checkout and check out exactly that commit.
# FETCH_HEAD works the same for a branch, a tag (annotated or not) and a full
# commit SHA, which `git clone --branch` does not accept.
fetch_ref() {
  if git -C "$SOURCE_DIR" remote get-url origin >/dev/null 2>&1; then
    git -C "$SOURCE_DIR" remote set-url origin "$REPO_URL"
  else
    git -C "$SOURCE_DIR" remote add origin "$REPO_URL"
  fi
  git -C "$SOURCE_DIR" fetch --quiet --depth 1 origin "$REF" \
    || err "Could not fetch '$REF' from $REPO_URL. Check the ref and your network, then try again."
  git -C "$SOURCE_DIR" checkout --quiet FETCH_HEAD \
    || err "Fetched '$REF' but could not check it out in $SOURCE_DIR."
}

sync_repo() {
  if [ -d "$SOURCE_DIR/.git" ]; then
    if repo_is_dirty; then
      warn "Using existing checkout without updating because it has local changes: $SOURCE_DIR"
      warn "The requested ref '$REF' was not applied."
      return
    fi
    info "Updating cached source checkout in $SOURCE_DIR"
    fetch_ref
    return
  fi

  if [ -e "$SOURCE_DIR" ]; then
    err "$SOURCE_DIR exists but is not a git checkout."
  fi

  mkdir -p "$IX_HOME"
  info "Fetching ix-codex-plugin into $SOURCE_DIR"
  git -c init.defaultBranch=main init --quiet "$SOURCE_DIR"
  fetch_ref
}

# `--ref <ref>` / `--ref=<ref>` is the installer's own; everything else is
# forwarded. Rebuilds "$@" without it.
expect_ref=0
for arg do
  shift
  if [ "$expect_ref" -eq 1 ]; then
    REF="$arg"
    REF_FROM_ENV=0
    expect_ref=0
    continue
  fi
  case "$arg" in
    --ref)
      expect_ref=1
      continue
      ;;
    --ref=*)
      REF="${arg#--ref=}"
      REF_FROM_ENV=0
      continue
      ;;
  esac
  set -- "$@" "$arg"
done
if [ "$expect_ref" -eq 1 ]; then
  err "--ref needs a branch, tag or commit."
fi

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  show_help
  exit 0
fi

check_ref
# A variable exported earlier in the shell keeps pinning every later run, so say
# where a non-default ref came from and how to go back to main.
if [ "$REF_FROM_ENV" -eq 1 ] && [ "$REF" != "main" ]; then
  warn "Installing '$REF' because IX_CODEX_REF is set in the environment; unset IX_CODEX_REF to install main."
fi
require_cmd git
PYTHON_BIN="$(find_python)"
sync_repo
info "Installing from $(git -C "$SOURCE_DIR" rev-parse HEAD) (requested: $REF)"

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
  set -- --plugin --hooks --mcp "$@"
fi

exec "$PYTHON_BIN" "$SOURCE_DIR/scripts/install_codex_integration.py" "$@"
