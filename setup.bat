@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed
echo Setup completed. Run start.bat to open LUNA.
pause
exit /b 0
:failed
echo Setup failed. Check Python 3.12, microphone package support, and network access.
pause
exit /b 1
