#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/scripts/install_codex_integration.py"

print_help() {
  cat <<'EOF'
ix-codex-plugin installer wrapper

Usage:
  ./install.sh --repo /path/to/project [--plugin] [--hooks] [--mcp] [--mode copy|symlink] [--force]
  ./install.sh --home [--plugin] [--hooks] [--mcp] [--mode copy|symlink] [--force]

Examples:
  ./install.sh --repo /path/to/project --plugin
  ./install.sh --repo /path/to/project --plugin --hooks
  ./install.sh --repo /path/to/project --plugin --hooks --mode symlink
  ./install.sh --home --plugin --hooks
  ./install.sh --home --hooks --mcp

Flags:
  --plugin   Copy/register the ix-memory Codex plugin in a local marketplace
  --hooks    Install the .codex hook bundle (session, prompt, pre/post tool, stop)
  --mcp      Install the ix-memory MCP server and print the codex mcp add command

Notes:
  - If none of --plugin, --hooks, or --mcp is passed, the installer defaults to --plugin.
  - --plugin does not activate the plugin in Codex. Restart Codex, then install or enable
    'ix-memory' from the marketplace before its skills appear.
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
