# One-time setup. Run from the project folder in PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 [-Edition base|languages]
#
# -Edition chooses the models to download (default: the edition this download
# names in edition.txt, or "base"):
#   base       English dictation, cleanup, speak-to-edit           ~1.4 GB
#   languages  base + the language pack: dictation in 40 languages,
#              language-aware cleanup, translation                  ~2.0 GB
# The language pack can also be added later:  keynone setup --edition languages
#
# Everything (Python, packages, models, caches) is installed inside this folder.
# Requires uv (https://docs.astral.sh/uv/) on PATH.

param(
    [ValidateSet("", "base", "languages")]
    [string]$Edition = ""
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"
Set-Location $KeynoneRoot

if (-not $Edition) {
    $marker = Join-Path $KeynoneRoot "edition.txt"
    $Edition = if (Test-Path $marker) { (Get-Content $marker -TotalCount 1).Trim() } else { "base" }
}
$size = if ($Edition -eq "languages") { "~2.0 GB" } else { "~1.4 GB" }

Write-Host "1/3  Python 3.12 (project-local)..."
uv python install 3.12.13 --no-bin --no-registry

Write-Host "2/3  Python packages..."
uv sync --frozen --no-dev

Write-Host "3/3  Models for the '$Edition' edition ($size, verified by SHA-256)..."
& "$KeynoneRoot\.venv\Scripts\keynone.exe" setup --edition $Edition
& "$KeynoneRoot\.venv\Scripts\keynone.exe" doctor

Write-Host ""
Write-Host "Done. Start Keynone by double-clicking Keynone.cmd (or: keynone listen)."
Write-Host "Settings: Keynone-settings.cmd, or Settings... in the tray menu."
