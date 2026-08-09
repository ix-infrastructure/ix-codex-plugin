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
  irm https://raw.githubusercontent.com/ix-infrastructure/ix-codex-plugin/main/codex-install.ps1 | iex

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

# Run a native command without letting its stderr abort the install.
#
# Windows PowerShell 5.1 turns everything a native command writes to stderr into
# an ErrorRecord, and the $ErrorActionPreference = "Stop" above makes that
# terminating. git writes ordinary progress there on success -- "From
# https://github.com/..." after a fetch, "Cloning into '...'" after a clone --
# so the installer aborted with NativeCommandError on commands that had actually
# worked, and told the user nothing except a line number. PowerShell 7 stopped
# treating native stderr as errors, which is why this only ever reproduced for
# some people.
#
# So: drop to Continue for the duration of the call, and flatten the merged
# streams to plain strings, which is what stops an ErrorRecord surviving as an
# error rather than as text. Then decide on $LASTEXITCODE, which is the only
# thing that actually reports whether the command succeeded. ForEach-Object
# rather than Select-Object here on purpose -- -First halts the native command
# with StopUpstreamCommandsException and loses the exit code with it.
#
# These calls previously had no exit-code check at all, so the stderr bug was
# also the only thing standing in for one: a genuinely failed fetch would have
# been just as invisible as a successful one was fatal.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Command @Arguments 2>&1 | ForEach-Object { "$_" }
    } finally {
        $ErrorActionPreference = $prevEap
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        $output | ForEach-Object { Write-Host "  $_" }
        Write-Err $FailureMessage
    }

    return $output
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    return Invoke-Native -Command "git" -Arguments $Arguments -FailureMessage $FailureMessage
}

function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) { return @("py", "-3") }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @("python") }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { return @("python3") }
    Write-Err "Python 3 is required to run the Codex installer."
}

function Repo-IsDirty {
    if (-not (Test-Path (Join-Path $SourceDir ".git"))) { return $false }
    # Not Invoke-Git: this one keeps stderr discarded rather than merged, since
    # merging it would let a git warning read as a modified file and silently
    # downgrade the install to "use the existing checkout". It still needs the
    # Continue window, because 2>$null on 5.1 discards the text but not the
    # terminating error the redirection itself produces. A status that cannot
    # run is treated as clean and left to the fetch below to report properly.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $status = & git -C $SourceDir status --short 2>$null
    } finally {
        $ErrorActionPreference = $prevEap
    }
    if ($LASTEXITCODE -ne 0) { return $false }
    return -not [string]::IsNullOrWhiteSpace(($status | Out-String))
}

function Sync-Repo {
    if (Test-Path (Join-Path $SourceDir ".git")) {
        if (Repo-IsDirty) {
            Write-Warn "Using existing checkout without updating because it has local changes: $SourceDir"
            return
        }
        Write-Ok "Updating cached source checkout in $SourceDir"
        $null = Invoke-Git -Arguments @("-C", $SourceDir, "remote", "set-url", "origin", $RepoUrl) -FailureMessage "Could not point $SourceDir at $RepoUrl."
        $null = Invoke-Git -Arguments @("-C", $SourceDir, "fetch", "--depth", "1", "origin", $Ref) -FailureMessage "Could not fetch $Ref from $RepoUrl. Check your network and try again."
        $null = Invoke-Git -Arguments @("-C", $SourceDir, "checkout", "--quiet", "FETCH_HEAD") -FailureMessage "Fetched $Ref but could not check it out in $SourceDir."
        return
    }

    if (Test-Path $SourceDir) {
        Write-Err "$SourceDir exists but is not a git checkout."
    }

    New-Item -ItemType Directory -Force -Path $IxHome | Out-Null
    Write-Ok "Cloning ix-codex-plugin into $SourceDir"
    $null = Invoke-Git -Arguments @("clone", "--depth", "1", "--branch", $Ref, $RepoUrl, $SourceDir) -FailureMessage "Could not clone $RepoUrl into $SourceDir."
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

# The same 5.1 stderr hazard, but this one cannot be captured: the Python
# installer's output is the user-facing result of the whole command and has to
# reach the console as it happens. So it gets the Continue window without the
# capture, and $LASTEXITCODE still decides the outcome. Without this, a Python
# that printed a single DeprecationWarning would abort here -- after the work was
# already done -- and never reach the exit below.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $pythonCmd[0] @pythonArgs
} finally {
    $ErrorActionPreference = $prevEap
}
exit $LASTEXITCODE
