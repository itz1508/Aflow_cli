param(
    [Parameter(Mandatory)]
    [string]$WheelPath,
    [string]$OutputDir = ".\aflow-judge-demo"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it with: winget install --id astral-sh.uv -e"
}

$wheel = (Resolve-Path -LiteralPath $WheelPath).Path
if ([IO.Path]::GetExtension($wheel) -ne ".whl") {
    throw "WheelPath must point to a .whl file."
}

$output = [IO.Path]::GetFullPath($OutputDir)
if (Test-Path -LiteralPath $output) {
    throw "Refusing to reuse existing output directory: $output"
}

uv tool run --from $wheel aflow demo --output-dir $output
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

uv tool run --from $wheel aflow validate-bundle --output-dir $output
exit $LASTEXITCODE
