@echo off
rem Stops the Keynone listener (and its engine, if loaded).
"%~dp0.venv\Scripts\python.exe" -m keynone stop
