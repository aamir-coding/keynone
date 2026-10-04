# Dot-source before running uv/python in this repo:   . .\scripts\env.ps1
#
# Keeps every toolchain artifact (uv cache, managed Python, model caches, temp
# files) inside the project folder so nothing is written elsewhere on the machine.

$KeynoneRoot = Split-Path -Parent $PSScriptRoot

$env:KEYNONE_HOME          = $KeynoneRoot
$env:UV_CACHE_DIR          = Join-Path $KeynoneRoot ".cache\uv"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $KeynoneRoot ".python"
$env:UV_PYTHON_BIN_DIR     = Join-Path $KeynoneRoot ".python\bin"   # safety net for shims
$env:UV_PYTHON_PREFERENCE  = "only-managed"
$env:UV_PYTHON_DOWNLOADS   = "manual"      # never auto-download (would add registry entries)
$env:UV_TOOL_DIR           = Join-Path $KeynoneRoot ".cache\uv-tools"
$env:UV_TOOL_BIN_DIR       = Join-Path $KeynoneRoot ".cache\uv-tools\bin"
$env:PIP_CACHE_DIR         = Join-Path $KeynoneRoot ".cache\pip"
$env:HF_HOME               = Join-Path $KeynoneRoot ".cache\huggingface"
$env:TMP                   = Join-Path $KeynoneRoot ".tmp"
$env:TEMP                  = $env:TMP

New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null

# First-time Python install (only needed once):
#   uv python install 3.12.13 --no-bin --no-registry
