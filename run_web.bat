@echo off
REM WFP Chad data assistant: opens the chat in your browser. It answers only with ANTHROPIC_API_KEY set.
REM Everyone signs in with an account. The first time, this file asks you to create yours (administrator);
REM add the others with: python manage_users.py add someone@cgiar.org --name "First Last"
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
"%PY%" manage_users.py first-admin
if errorlevel 1 (
    echo The first account could not be created.
    pause
    exit /b 1
)
start "" "http://127.0.0.1:8011"
"%PY%" web\app.py
pause
