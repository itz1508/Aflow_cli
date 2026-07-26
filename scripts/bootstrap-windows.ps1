param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
if (-not [Environment]::Is64BitOperatingSystem) { throw "A-Flow supports Windows x64 only." }
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "WinGet is required." }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
}
py -3.12 -m pip install --upgrade "theoneshot-aflow==$Version"
aflow setup --yes
aflow doctor
