@echo off
title [CTRL] Mode chooser
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0scripts\Experiment-Chooser.ps1"
