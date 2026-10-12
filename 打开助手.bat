@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0runtime\python.exe" (
    "%~dp0runtime\python.exe" -X utf8 "%~dp0start_hka.pyw" --console %*
    exit /b
)
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0start_hka.pyw" --console %*
    exit /b
)
echo Runtime missing. Extract the complete portable ZIP before starting HKA.
pause
exit /b 1
