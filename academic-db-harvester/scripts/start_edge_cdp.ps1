<#
.SYNOPSIS
  Start Microsoft Edge with CDP remote debugging for the Academic DB Harvester.

.DESCRIPTION
  Chromium 136+ (including current Edge 152) ignores --remote-debugging-port when
  the browser is started with the DEFAULT user-data-dir. This launcher starts Edge
  with a DEDICATED profile directory (default: %USERPROFILE%\edge-cdp\profile) so
  that the CDP endpoint http://127.0.0.1:9222 actually comes up.

  On first run it copies cookies and login state from the main Edge profile so the
  existing IEEE Xplore / institutional login is preserved. Afterwards the debug
  browser keeps its own session - no need to close your normal Edge windows.

  Three flags are required for a usable CDP endpoint, and the launcher sets all
  three: --remote-debugging-port (the port), --remote-allow-origins=* (Chromium
  rejects CDP WebSocket upgrades that carry an Origin header otherwise), and a
  dedicated --user-data-dir (Chromium 136+ ignores the port on the default one).

.PARAMETER Port
  CDP port. Default: 9222.

.PARAMETER ProfileDir
  Dedicated user-data-dir for the debug browser.
  Default: %USERPROFILE%\edge-cdp\profile

.PARAMETER EdgePath
  Explicit path to msedge.exe (auto-detected when omitted).

.PARAMETER Url
  URL opened on launch. Default: https://ieeexplore.ieee.org

.PARAMETER SkipCookieCopy
  Do not copy cookies/login state from the main Edge profile on first run.

.PARAMETER TimeoutSec
  Seconds to wait for the CDP endpoint. Default: 45.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_edge_cdp.ps1

.EXAMPLE
  .\scripts\start_edge_cdp.ps1 -Port 9333 -SkipCookieCopy
#>
param(
    [int]$Port = 9222,
    [string]$ProfileDir = (Join-Path $env:USERPROFILE "edge-cdp\profile"),
    [string]$EdgePath = "",
    [string]$Url = "https://ieeexplore.ieee.org",
    [switch]$SkipCookieCopy,
    [int]$TimeoutSec = 45
)

$ErrorActionPreference = "Continue"

function Write-Step($msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err2($msg) { Write-Host "[-] $msg" -ForegroundColor Red }

function Find-EdgePath {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    $cmd = Get-Command msedge.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-CdpVersion([int]$Port) {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 3
    } catch {
        return $null
    }
}

function Get-CdpTabs([int]$Port) {
    try {
        $tabs = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/list" -TimeoutSec 5
        return @($tabs)
    } catch {
        return @()
    }
}

function Copy-EdgeLoginState([string]$DestProfile) {
    $mainRoot = Join-Path $env:LOCALAPPDATA "Microsoft\Edge\User Data"
    if (-not (Test-Path -LiteralPath $mainRoot)) {
        Write-Warn2 "Main Edge profile not found at $mainRoot - cookie copy skipped."
        return
    }
    $destDefault = Join-Path $DestProfile "Default"
    $destNetwork = Join-Path $destDefault "Network"
    New-Item -ItemType Directory -Force -Path $destNetwork | Out-Null

    $items = @(
        @{ Src = "Local State";                  Dst = $DestProfile },
        @{ Src = "Default\Preferences";          Dst = $destDefault },
        @{ Src = "Default\Secure Preferences";   Dst = $destDefault },
        @{ Src = "Default\Network\Cookies";      Dst = $destNetwork },
        @{ Src = "Default\Login Data";           Dst = $destDefault },
        @{ Src = "Default\Web Data";             Dst = $destDefault }
    )
    foreach ($it in $items) {
        $src = Join-Path $mainRoot $it.Src
        if (-not (Test-Path -LiteralPath $src)) { continue }
        $name = Split-Path $it.Src -Leaf
        try {
            Copy-Item -LiteralPath $src -Destination (Join-Path $it.Dst $name) -Force -ErrorAction Stop
            Write-Ok "copied $($it.Src)"
        } catch {
            Write-Warn2 "could not copy $($it.Src) (Edge may be running) - sign in again in the debug window if needed"
        }
    }
}

# -- 1. Is a CDP debug browser already listening? --
Write-Step "Checking CDP endpoint on port $Port ..."
$ver = Get-CdpVersion $Port
if ($ver) {
    Write-Ok "CDP is already available: $($ver.Browser)"
    $tabs = Get-CdpTabs $Port
    $ieee = @($tabs | Where-Object { $_.url -like "https://ieeexplore.ieee.org*" })
    if ($ieee.Count -gt 0) {
        Write-Ok "IEEE Xplore tab found ($($ieee.Count)) - ready for the harvester."
    } else {
        Write-Warn2 "No IEEE Xplore tab in the debug browser. Opening $Url ..."
        if (-not $EdgePath) { $EdgePath = Find-EdgePath }
        if ($EdgePath) {
            Start-Process -FilePath $EdgePath -ArgumentList @(
                "--remote-debugging-port=$Port",
                "--remote-allow-origins=*",
                "--user-data-dir=`"$ProfileDir`"",
                $Url
            ) | Out-Null
        } else {
            Write-Err2 "msedge.exe not found - open $Url manually in the debug browser."
        }
    }
    exit 0
}

# -- 2. Resolve msedge.exe --
if (-not $EdgePath) { $EdgePath = Find-EdgePath }
if (-not $EdgePath) {
    Write-Err2 "msedge.exe not found. Pass -EdgePath <full path to msedge.exe>."
    exit 1
}
Write-Ok "Edge: $EdgePath"

# -- 3. Prepare the dedicated profile --
$freshProfile = -not (Test-Path -LiteralPath (Join-Path $ProfileDir "Local State"))
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
if ($freshProfile -and -not $SkipCookieCopy) {
    Write-Step "First run: copying login state from your main Edge profile ..."
    Copy-EdgeLoginState $ProfileDir
} elseif ($freshProfile) {
    Write-Warn2 "Fresh profile created (cookie copy skipped) - sign in inside the debug window."
}

# -- 4. Launch Edge with CDP on the dedicated profile --
Write-Step "Launching Edge with CDP (dedicated profile) ..."
Write-Host "    profile : $ProfileDir"
Write-Host "    url     : $Url"
Start-Process -FilePath $EdgePath -ArgumentList @(
    "--remote-debugging-port=$Port",
    "--remote-allow-origins=*",
    "--user-data-dir=`"$ProfileDir`"",
    "--no-first-run",
    "--no-default-browser-check",
    $Url
) | Out-Null

# -- 5. Wait for CDP to come up --
Write-Step "Waiting for CDP on port $Port (up to ${TimeoutSec}s) ..."
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$ver = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    $ver = Get-CdpVersion $Port
    if ($ver) { break }
}

if (-not $ver) {
    Write-Err2 "CDP did not come up on port $Port."
    Write-Host  "    - If Edge with this profile was already open WITHOUT the debug flag,"
    Write-Host  "      close ALL its windows (check Task Manager for msedge.exe using edge-cdp)"
    Write-Host  "      and run this script again."
    Write-Host  "    - Make sure no other program uses port $Port."
    exit 1
}

Write-Ok "CDP ready: $($ver.Browser)"
$tabs = Get-CdpTabs $Port
$ieee = @($tabs | Where-Object { $_.url -like "https://ieeexplore.ieee.org*" })
if ($ieee.Count -gt 0) {
    Write-Ok "IEEE Xplore tab is open. Sign in there if needed (first run only)."
} else {
    Write-Warn2 "IEEE tab not detected yet - give the page a moment, or open $Url manually."
}

# -- 6. Optional: verify with the bundled detector --
$detector = Join-Path $PSScriptRoot "cdp_detector.py"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if ((Test-Path -LiteralPath $detector) -and $python) {
    Write-Step "Running cdp_detector.py for verification ..."
    try { & $python.Source $detector --port $Port } catch { Write-Warn2 "detector failed: $($_.Exception.Message)" }
}

Write-Ok "Done. Example: python scripts/harvester.py --keyword <terms> --count 50"
exit 0
