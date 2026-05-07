[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$InstallerArgs
)

$ErrorActionPreference = "Stop"

$GithubOrg = "ix-infrastructure"
$GithubRepo = "ix-codex-plugin"
$RepoUrl = "https://github.com/$GithubOrg/$GithubRepo.git"
$Ref = if ($env:IX_CODEX_REF) { $env:IX_CODEX_REF } else { "main" }
$IxHome = if ($env:IX_HOME) { $env:IX_HOME } else { Join-Path $env:USERPROFILE ".ix" }
$SourceDir = Join-Path $IxHome "codex-plugin-source"

function Write-Ok($msg) { Write-Host "  [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function Write-Err($msg) {
    Write-Host "  [error] $msg" -ForegroundColor Red
    exit 1
}

function Show-HostedHelp {
    @"
ix-codex-plugin hosted installer

Usage:
  irm https://ix-infra.com/codex-install.ps1 | iex

Behavior:
  - Clones or updates ix-codex-plugin into $SourceDir
  - Runs scripts/install_codex_integration.py from that checkout
  - Defaults to: --home --plugin --hooks --mcp

Options:
  All remaining arguments are forwarded to install_codex_integration.py.

Environment:
  IX_CODEX_REF   Branch or tag to install from (default: main)
  IX_HOME        Base directory for the cached source checkout (default: ~/.ix)
"@
}

function Require-Command($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Err "$name is required but was not found."
    }
}

function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) { return @("py", "-3") }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @("python") }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { return @("python3") }
    Write-Err "Python 3 is required to run the Codex installer."
}

function Repo-IsDirty {
    if (-not (Test-Path (Join-Path $SourceDir ".git"))) { return $false }
    $status = git -C $SourceDir status --short 2>$null
    return -not [string]::IsNullOrWhiteSpace(($status | Out-String))
}

function Sync-Repo {
    if (Test-Path (Join-Path $SourceDir ".git")) {
        if (Repo-IsDirty) {
            Write-Warn "Using existing checkout without updating because it has local changes: $SourceDir"
            return
        }
        Write-Ok "Updating cached source checkout in $SourceDir"
        git -C $SourceDir remote set-url origin $RepoUrl
        git -C $SourceDir fetch --depth 1 origin $Ref
        git -C $SourceDir checkout --quiet FETCH_HEAD
        return
    }

    if (Test-Path $SourceDir) {
        Write-Err "$SourceDir exists but is not a git checkout."
    }

    New-Item -ItemType Directory -Force -Path $IxHome | Out-Null
    Write-Ok "Cloning ix-codex-plugin into $SourceDir"
    git clone --depth 1 --branch $Ref $RepoUrl $SourceDir
}

function Ensure-Defaults([string[]]$args) {
    $result = [System.Collections.Generic.List[string]]::new()
    $hasTarget = $false
    $hasAction = $false

    foreach ($arg in $args) {
        if ($arg -eq "--home" -or $arg -eq "--repo") { $hasTarget = $true }
        if ($arg -eq "--plugin" -or $arg -eq "--hooks" -or $arg -eq "--mcp") { $hasAction = $true }
    }

    if (-not $hasTarget) { $result.Add("--home") }
    if (-not $hasAction) {
        $result.Add("--plugin")
        $result.Add("--hooks")
        $result.Add("--mcp")
    }
    foreach ($arg in $args) { $result.Add($arg) }
    return $result.ToArray()
}

if ($InstallerArgs.Count -gt 0 -and ($InstallerArgs[0] -eq "--help" -or $InstallerArgs[0] -eq "-h")) {
    Show-HostedHelp
    exit 0
}

Require-Command git
$pythonCmd = Get-PythonCommand
Sync-Repo

$effectiveArgs = Ensure-Defaults $InstallerArgs
$installer = Join-Path $SourceDir "scripts/install_codex_integration.py"
$pythonArgs = @()
if ($pythonCmd.Count -gt 1) {
    $pythonArgs += $pythonCmd[1..($pythonCmd.Count - 1)]
}
$pythonArgs += $installer
$pythonArgs += $effectiveArgs

& $pythonCmd[0] @pythonArgs
exit $LASTEXITCODE
