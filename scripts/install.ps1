# Install CO2-Response on Windows, for this user, in its own Python environment.
#
#   irm https://raw.githubusercontent.com/shrlak/CO2-Response/main/scripts/install.ps1 | iex
#   .\scripts\install.ps1                   # from a downloaded copy of the repository
#   .\scripts\install.ps1 -TerminalOnly     # without the Qt desktop window (smaller)
#
# Afterwards (in a new terminal):  co2response-tui --sim    or    co2response --sim
param([switch]$TerminalOnly)
$ErrorActionPreference = "Stop"

$Source = "git+https://github.com/shrlak/CO2-Response"
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "..\pyproject.toml"))) { $Source = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$Extra = if ($TerminalOnly) { "" } else { "[desktop]" }

$Py = $null
foreach ($c in @("py -3", "python")) {
  try {
    $ok = & cmd /c "$c -c ""import sys; print(sys.version_info >= (3, 10))""" 2>$null
    if ($ok -eq "True") { $Py = $c; break }
  } catch {}
}
if (-not $Py) { Write-Error "Python 3.10 or newer is needed: https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."; exit 1 }

$Venv = if ($env:CO2R_VENV) { $env:CO2R_VENV } else { Join-Path $env:LOCALAPPDATA "CO2-Response\venv" }
Write-Host "Installing CO2-Response$Extra into $Venv"
& cmd /c "$Py -m venv ""$Venv"""
$VPy = Join-Path $Venv "Scripts\python.exe"
& $VPy -m pip install --quiet --upgrade pip
if (Test-Path $Source) { & $VPy -m pip install --quiet "$Source$Extra" } else { & $VPy -m pip install --quiet "co2response$Extra @ $Source" }

$Bin = Join-Path $Venv "Scripts"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($UserPath -split ";" | Where-Object { $_ -eq $Bin })) {
  [Environment]::SetEnvironmentVariable("Path", "$UserPath;$Bin", "User")
  Write-Host "Added $Bin to your PATH (open a new terminal to use it)."
}
Write-Host ""
Write-Host "Installed. Try it (no hardware needed):"
Write-Host "  co2response-tui --sim             the monitor full-screen in this terminal"
Write-Host "  co2response --fast-test normal    a whole simulated rebreathing test in a second, with its report"
if (-not $TerminalOnly) { Write-Host "  co2response --sim                the desktop window" }
