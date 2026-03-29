#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/scripts/install_codex_integration.py"

print_help() {
  cat <<'EOF'
ix-codex-plugin installer wrapper

Usage:
  ./install.sh --repo /path/to/project [--plugin] [--hooks] [--mode copy|symlink] [--force]
  ./install.sh --home [--plugin] [--hooks] [--mode copy|symlink] [--force]

Examples:
  ./install.sh --repo /path/to/project --plugin
  ./install.sh --repo /path/to/project --plugin --hooks
  ./install.sh --repo /path/to/project --plugin --hooks --mode symlink
  ./install.sh --home --plugin --hooks

Notes:
  - If neither --plugin nor --hooks is passed, the installer defaults to --plugin.
  - This wrapper forwards all arguments to scripts/install_codex_integration.py.
EOF
}

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python3 or python is required to run the installer." >&2
  exit 1
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_help
  echo
  exec "$PYTHON_BIN" "$INSTALLER" --help
fi

exec "$PYTHON_BIN" "$INSTALLER" "$@"
