<#
.SYNOPSIS
    Install or update the skill from this repository.

.DESCRIPTION
    Copies academic-db-harvester/ into a skills directory that the agent
    scans.

    The repository is the single source of truth. Edit here, run this script,
    and never edit the installed copy: the next update mirrors the source
    over it (/MIR), so anything changed by hand in the installed copy is
    discarded. That is the point - one copy, no drift.

.PARAMETER Target
    Directory that holds the skills.
    Default: $HOME\.agents\skills
    Other locations: $HOME\.config\opencode\skills, .\.opencode\skills,
    .\.claude\skills, .\.agents\skills

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
      -Target "D:\project\.opencode\skills"
#>
[CmdletBinding()]
param(
    [string]$Target = (Join-Path $HOME ".agents\skills")
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $repoRoot "academic-db-harvester"
$dst = Join-Path $Target "academic-db-harvester"

if (-not (Test-Path $src)) {
    throw "skill source not found: $src"
}
if (-not (Test-Path $Target)) {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
}

Write-Host "source : $src"
Write-Host "target : $dst"

# /MIR mirrors the tree, so files removed from the source are removed here
# too. __pycache__ is never mirrored - it is build output, not source.
& robocopy $src $dst /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
$code = $LASTEXITCODE

# robocopy: 0 = nothing to do, 1 = files copied, 2 = extras deleted,
# 3 = both, 4-7 = still success, 8+ = failure.
if ($code -ge 8) {
    throw "robocopy failed with exit code $code"
}
if ($code -ge 1) {
    Write-Host "installed (robocopy code $code)"
} else {
    Write-Host "already up to date"
}

# A skill is only discoverable when SKILL.md is present and the folder name
# matches the skill name inside it.
$skill = Join-Path $dst "SKILL.md"
if (-not (Test-Path $skill)) {
    throw "installed copy has no SKILL.md - the install is broken"
}
$name = (Select-String -Path $skill -Pattern '^name:\s*(\S+)' | Select-Object -First 1).Matches.Groups[1].Value
if ($name -ne (Split-Path $dst -Leaf)) {
    Write-Warning "the skill name '$name' does not match the folder name '$(Split-Path $dst -Leaf)' - loaders may not pick it up"
}
Write-Host "ok -> $dst"
