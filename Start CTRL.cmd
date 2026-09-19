@echo off
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0scripts\Experiment-Chooser.ps1"
if errorlevel 1 pause
