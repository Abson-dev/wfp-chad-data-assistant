@echo off
REM WFP Chad data assistant: opens the chat in your browser. It answers only with ANTHROPIC_API_KEY set.
REM On this computer no password is asked; on a server, set APP_PASSWORD and HOST instead of using this file.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title WFP Chad data assistant
set "PY=%LOCALAPPDATA%\miniforge3\envs\ensa-agent\python.exe"
if not exist "%PY%" (
    echo The conda environment ensa-agent was not found. Create it: conda env create -f environment.yml
    pause
    exit /b 1
)
set "WFP_AGENT_ALLOW_UNLOCKED=true"
start "" "http://127.0.0.1:8011"
"%PY%" web\app.py
pause
