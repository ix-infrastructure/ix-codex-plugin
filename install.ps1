[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$InstallerArgs
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path $scriptDir "scripts/install_codex_integration.py"

function Show-WrapperHelp {
    @"
ix-codex-plugin installer wrapper

Usage:
  .\install.ps1 --repo C:\path\to\project [--plugin] [--hooks] [--mcp] [--mode copy|symlink] [--force]
  .\install.ps1 --home [--plugin] [--hooks] [--mcp] [--mode copy|symlink] [--force]

Examples:
  .\install.ps1 --repo C:\path\to\project --plugin
  .\install.ps1 --repo C:\path\to\project --plugin --hooks
  .\install.ps1 --repo C:\path\to\project --plugin --hooks --mode symlink
  .\install.ps1 --home --plugin --hooks
  .\install.ps1 --home --hooks --mcp

Flags:
  --plugin   Copy/register the ix-memory Codex plugin in a local marketplace
  --hooks    Install the .codex hook bundle (session, prompt, pre/post tool, stop)
  --mcp      Install the ix-memory MCP server and print the codex mcp add command

Notes:
  - If none of --plugin, --hooks, or --mcp is passed, the installer defaults to --plugin.
  - --plugin does not activate the plugin in Codex. Restart Codex, then install or enable
    'ix-memory' from the marketplace before its skills appear.
  - This wrapper forwards all arguments to scripts/install_codex_integration.py.
"@
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    if ($InstallerArgs.Count -gt 0 -and ($InstallerArgs[0] -eq "--help" -or $InstallerArgs[0] -eq "-h")) {
        Show-WrapperHelp
        ""
    }
    & py -3 $installer @InstallerArgs
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    if ($InstallerArgs.Count -gt 0 -and ($InstallerArgs[0] -eq "--help" -or $InstallerArgs[0] -eq "-h")) {
        Show-WrapperHelp
        ""
    }
    & python $installer @InstallerArgs
    exit $LASTEXITCODE
}

if (Get-Command python3 -ErrorAction SilentlyContinue) {
    if ($InstallerArgs.Count -gt 0 -and ($InstallerArgs[0] -eq "--help" -or $InstallerArgs[0] -eq "-h")) {
        Show-WrapperHelp
        ""
    }
    & python3 $installer @InstallerArgs
    exit $LASTEXITCODE
}

Write-Error "Python 3 is required to run the installer."
