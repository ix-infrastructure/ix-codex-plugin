# Copyright 2026 Ix Infrastructure Inc.

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

  `$env:IX_CODEX_REF = "<tag>"; try { irm https://raw.githubusercontent.com/ix-infrastructure/ix-codex-plugin/<tag>/codex-install.ps1 | iex } finally { Remove-Item Env:IX_CODEX_REF }
  .\codex-install.ps1 --ref <tag>

Behavior:
  - Fetches the requested ref of ix-codex-plugin into $SourceDir
  - Runs scripts/install_codex_integration.py from that checkout
  - Defaults to: --home --plugin --hooks --mcp

Options:
  --ref <ref>    Branch, tag or full commit SHA to install (overrides IX_CODEX_REF)
  All remaining arguments are forwarded to install_codex_integration.py.

Environment:
  IX_CODEX_REF   Branch, tag or full commit SHA to install from (default: main)
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
# The precise trigger matters, because it is narrower than "5.1 treats native
# stderr as an error". Measured on 5.1.26100, with $ErrorActionPreference =
# "Stop", against a command that writes to stderr and exits 0:
#
#     cmd /c "echo x 1>&2"                      -> no throw
#     $v = cmd /c "echo x 1>&2"                 -> no throw
#     $v = cmd /c "echo x 1>&2" 2>$null         -> THROWS RemoteException
#     $v = cmd /c "echo x 1>&2" 2>&1            -> THROWS RemoteException
#     cmd /c "echo x 1>&2" 2>&1 | Out-Null      -> THROWS RemoteException
#
# It is *redirecting* the stream that materialises the ErrorRecord, not writing
# to it. So the bare `git fetch` / `git clone` calls this replaced were never at
# risk from their own progress output, and the one site that genuinely was is
# Repo-IsDirty below, which already had `2>$null` -- which is why that one keeps
# its own inline Continue window rather than coming through here.
#
# Which means this function introduces the hazard (`2>&1 |`) in order to capture
# output for the failure path, and the Continue window is what makes that safe.
# Do not remove it on the grounds that the calls did not need it before: they
# did not, and they do now. ForEach-Object rather than Select-Object on purpose
# -- -First halts the native command with StopUpstreamCommandsException and
# loses the exit code with it.
#
# The real defect at these call sites was the missing exit-code check: they had
# none, so a genuinely failed fetch was silent. $LASTEXITCODE is the only thing
# that reports whether a native command succeeded.
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

# Returns the command as an array: the leading comma stops PowerShell from
# unrolling a one-element array into a bare string, which would make
# $pythonCmd[0] the first character ("p") rather than "python".
function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) { return ,@("py", "-3") }
    if (Get-Command python -ErrorAction SilentlyContinue) { return ,@("python") }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { return ,@("python3") }
    Write-Err "Python 3 is required to run the Codex installer."
}

function Repo-IsDirty {
    if (-not (Test-Path (Join-Path $SourceDir ".git"))) { return $false }
    # This is the site the reported NativeCommandError actually came from: of
    # every git call in this script, it was the only one already redirecting a
    # stream, and per the measurements above that is what makes 5.1 raise.
    #
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

# `--ref <ref>` / `--ref=<ref>` is the installer's own; everything else is
# forwarded to install_codex_integration.py.
function Split-RefArgument([string[]]$Arguments) {
    $rest = [System.Collections.Generic.List[string]]::new()
    $requested = $null
    $expectRef = $false
    foreach ($arg in $Arguments) {
        if ($expectRef) {
            $requested = $arg
            $expectRef = $false
            continue
        }
        if ($arg -ceq "--ref") {
            $expectRef = $true
            continue
        }
        if ($arg.StartsWith("--ref=", [System.StringComparison]::Ordinal)) {
            $requested = $arg.Substring(6)
            continue
        }
        $rest.Add($arg)
    }
    if ($expectRef) { Write-Err "--ref needs a branch, tag or commit." }
    return @{ Ref = $requested; Rest = $rest.ToArray() }
}

# The ref ends up as an argument to `git fetch`, so it must not be read as an
# option there, and must name one ref rather than a `src:dst` refspec.
function Assert-Ref([string]$Value) {
    if ([string]::IsNullOrEmpty($Value) -or $Value.StartsWith("-") -or $Value.Contains(":") -or $Value -match '\s') {
        Write-Err "Not a branch, tag or commit: '$Value'."
    }
}

# Fetch $Ref into the existing checkout and check out exactly that commit.
# FETCH_HEAD works the same for a branch, a tag (annotated or not) and a full
# commit SHA, which `git clone --branch` does not accept.
function Fetch-Ref {
    # Same stderr-discarding Continue window as Repo-IsDirty: only the exit
    # status matters, and a checkout from an interrupted first run has no origin.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $null = & git -C $SourceDir remote get-url origin 2>$null
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $remoteVerb = if ($LASTEXITCODE -eq 0) { "set-url" } else { "add" }
    $null = Invoke-Git -Arguments @("-C", $SourceDir, "remote", $remoteVerb, "origin", $RepoUrl) -FailureMessage "Could not point $SourceDir at $RepoUrl."
    $null = Invoke-Git -Arguments @("-C", $SourceDir, "fetch", "--quiet", "--depth", "1", "origin", $Ref) -FailureMessage "Could not fetch '$Ref' from $RepoUrl. Check the ref and your network, then try again."
    $null = Invoke-Git -Arguments @("-C", $SourceDir, "checkout", "--quiet", "FETCH_HEAD") -FailureMessage "Fetched '$Ref' but could not check it out in $SourceDir."
}

function Sync-Repo {
    if (Test-Path (Join-Path $SourceDir ".git")) {
        if (Repo-IsDirty) {
            Write-Warn "Using existing checkout without updating because it has local changes: $SourceDir"
            Write-Warn "The requested ref '$Ref' was not applied."
            return
        }
        Write-Ok "Updating cached source checkout in $SourceDir"
        Fetch-Ref
        return
    }

    if (Test-Path $SourceDir) {
        Write-Err "$SourceDir exists but is not a git checkout."
    }

    New-Item -ItemType Directory -Force -Path $IxHome | Out-Null
    Write-Ok "Fetching ix-codex-plugin into $SourceDir"
    $null = Invoke-Git -Arguments @("-c", "init.defaultBranch=main", "init", "--quiet", $SourceDir) -FailureMessage "Could not create a git checkout in $SourceDir."
    Fetch-Ref
}

# Not `$args`: that is PowerShell's automatic variable, and a parameter of that
# name arrives empty, silently replacing every user argument with the defaults.
function Ensure-Defaults([string[]]$Arguments) {
    $result = [System.Collections.Generic.List[string]]::new()
    $hasTarget = $false
    $hasAction = $false

    foreach ($arg in $Arguments) {
        if ($arg -eq "--home" -or $arg -eq "--repo") { $hasTarget = $true }
        if ($arg -eq "--plugin" -or $arg -eq "--hooks" -or $arg -eq "--mcp") { $hasAction = $true }
    }

    if (-not $hasTarget) { $result.Add("--home") }
    if (-not $hasAction) {
        $result.Add("--plugin")
        $result.Add("--hooks")
        $result.Add("--mcp")
    }
    foreach ($arg in $Arguments) { $result.Add($arg) }
    return $result.ToArray()
}

$parsedArgs = Split-RefArgument $InstallerArgs
$forwardArgs = $parsedArgs.Rest
$refFromEnv = -not [string]::IsNullOrEmpty($env:IX_CODEX_REF)
if ($null -ne $parsedArgs.Ref) {
    $Ref = $parsedArgs.Ref
    $refFromEnv = $false
}

if ($forwardArgs.Count -gt 0 -and ($forwardArgs[0] -eq "--help" -or $forwardArgs[0] -eq "-h")) {
    Show-HostedHelp
    exit 0
}

Assert-Ref $Ref
# $env:IX_CODEX_REF lives for the whole session, so a later plain `irm | iex`
# meant to install main would quietly reinstall the pinned ref. Say so.
if ($refFromEnv -and $Ref -cne "main") {
    Write-Warn "Installing '$Ref' because IX_CODEX_REF is set in this session; run 'Remove-Item Env:IX_CODEX_REF' to install main."
}
Require-Command git
$pythonCmd = Get-PythonCommand
Sync-Repo
$installedCommit = Invoke-Git -Arguments @("-C", $SourceDir, "rev-parse", "HEAD") -FailureMessage "Could not read the checked-out commit in $SourceDir."
Write-Ok "Installing from $(@($installedCommit)[0]) (requested: $Ref)"

$effectiveArgs = Ensure-Defaults $forwardArgs
$installer = Join-Path $SourceDir "scripts/install_codex_integration.py"
$pythonArgs = @()
if ($pythonCmd.Count -gt 1) {
    $pythonArgs += $pythonCmd[1..($pythonCmd.Count - 1)]
}
$pythonArgs += $installer
$pythonArgs += $effectiveArgs

# This call is deliberately left uncaptured: the Python installer's output is the
# user-facing result of the whole command and has to reach the console as it
# happens, not be replayed afterwards.
#
# Which, per the measurements above, also means it is not exposed to the 5.1
# hazard -- an uncaptured native command writing to stderr does not throw, so a
# Python printing a DeprecationWarning would not have aborted here. The Continue
# window stays as a guard for the stream this script does not control, and
# because $LASTEXITCODE below is what decides the outcome either way.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $pythonCmd[0] @pythonArgs
} finally {
    $ErrorActionPreference = $prevEap
}
exit $LASTEXITCODE
