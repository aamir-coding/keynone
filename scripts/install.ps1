# One-time setup. Run from the project folder in PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
#
# Everything (Python, packages, models, caches) is installed inside this folder.
# Requires uv (https://docs.astral.sh/uv/) on PATH.

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"
Set-Location $KeynoneRoot

Write-Host "1/3  Python 3.12 (project-local)..."
uv python install 3.12.13 --no-bin --no-registry

Write-Host "2/3  Python packages..."
uv sync --frozen

Write-Host "3/3  Speech + language models (~1.4 GB, verified by SHA-256)..."
& "$KeynoneRoot\.venv\Scripts\keynone.exe" setup
& "$KeynoneRoot\.venv\Scripts\keynone.exe" doctor

Write-Host ""
Write-Host "Done. Start Keynone by double-clicking Keynone.cmd (or: keynone listen)."
