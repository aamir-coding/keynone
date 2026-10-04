@echo off
rem Starts the Keynone hotkey listener in the background (tray icon, no console).
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Keynone is not installed yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m keynone listen
