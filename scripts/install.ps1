# One-time setup (and updates). Run from the Keynone folder in PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 [-Edition base|languages] [-Dev]
#
# -Edition chooses the models to download (default: the edition this download
# names in edition.txt, or "base"):
#   base       English dictation, cleanup, speak-to-edit           ~1.4 GB
#   languages  base + the language pack: dictation in 40 languages,
#              language-aware cleanup, translation                  ~2.0 GB
# The language pack can also be added later:  keynone setup --edition languages
#
# -Dev also installs the developer tools (pytest, ruff, mypy) and keeps uv's
# download cache, for working on Keynone itself (see CONTRIBUTING.md). Without
# it, only what the app needs is installed and the download cache is cleared.
#
# Everything (Python, packages, models, caches) is installed inside this folder.
# Requires uv (https://docs.astral.sh/uv/) on PATH.

param(
    [ValidateSet("", "base", "languages")]
    [string]$Edition = "",
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"
Set-Location $KeynoneRoot

if (-not $Edition) {
    $marker = Join-Path $KeynoneRoot "edition.txt"
    $Edition = if (Test-Path $marker) { (Get-Content $marker -TotalCount 1).Trim() } else { "base" }
}
$size = if ($Edition -eq "languages") { "~2.0 GB" } else { "~1.4 GB" }
$keynone = Join-Path $KeynoneRoot ".venv\Scripts\keynone.exe"

if (Test-Path $keynone) {
    # Updating: a running Keynone keeps its program files open.
    & $keynone stop | Out-Null
}

function Assert-Ok($step) {
    if ($LASTEXITCODE -ne 0) { throw "$step failed (exit code $LASTEXITCODE). Run this script again to retry." }
}

Write-Host "1/3  Python 3.12 (project-local)..."
uv python install 3.12.13 --no-bin --no-registry
Assert-Ok "Installing Python"

Write-Host "2/3  Python packages..."
if ($Dev) { uv sync --frozen } else { uv sync --frozen --no-dev }
Assert-Ok "Installing the Python packages"

Write-Host "3/3  Models for the '$Edition' edition ($size, verified by SHA-256)..."
& $keynone setup --edition $Edition
Assert-Ok "Downloading the models"
& $keynone doctor

if (-not $Dev) {
    # The packages are installed; keeping their downloads would only take up space.
    uv --quiet cache clean
    Get-ChildItem -Path (Join-Path $KeynoneRoot ".tmp") -Force -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Done. Start Keynone by double-clicking Keynone.cmd (or: keynone listen)."
Write-Host "Settings: Keynone-settings.cmd, or Settings... in the tray menu."
