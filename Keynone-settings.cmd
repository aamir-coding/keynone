@echo off
rem Opens the Keynone settings window (it edits data\config.toml, like a text editor would).
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Keynone is not installed yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m keynone settings
