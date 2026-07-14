@echo off
REM Thin wrapper: invoke mutagen with the project's venv Python, forwarding args.
REM Works from any cwd -- %~dp0 resolves to the folder holding this .bat.
setlocal
set "MUTAGEN_ROOT=%~dp0"
set "PY=%MUTAGEN_ROOT%.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [mutagen] venv not found at %PY%
    echo Run: uv venv --python 3.12 .venv ^&^& uv pip install -e .
    exit /b 1
)

"%PY%" -m mutagen.cli %*
exit /b %ERRORLEVEL%
