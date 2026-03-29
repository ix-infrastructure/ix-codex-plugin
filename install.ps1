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
  .\install.ps1 --repo C:\path\to\project [--plugin] [--hooks] [--mode copy|symlink] [--force]
  .\install.ps1 --home [--plugin] [--hooks] [--mode copy|symlink] [--force]

Examples:
  .\install.ps1 --repo C:\path\to\project --plugin
  .\install.ps1 --repo C:\path\to\project --plugin --hooks
  .\install.ps1 --repo C:\path\to\project --plugin --hooks --mode symlink
  .\install.ps1 --home --plugin --hooks

Notes:
  - If neither --plugin nor --hooks is passed, the installer defaults to --plugin.
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
