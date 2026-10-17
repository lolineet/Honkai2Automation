@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0runtime\python.exe" (
    "%~dp0runtime\python.exe" -X utf8 "%~dp0start_hka.pyw" --console %*
    goto finished
)
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0start_hka.pyw" --console %*
    goto finished
)
echo Runtime missing. Extract the complete portable ZIP before starting HKA.
pause
exit /b 1

:finished
set "hka_exit=%errorlevel%"
if not "%hka_exit%"=="0" (
    echo HKA failed to start. Please copy the error above when reporting the problem.
    pause
)
exit /b %hka_exit%
