@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-py312\Scripts\python.exe" (
    echo Run setup.bat with Python 3.12 64-bit first.
    pause
    exit /b 1
)
".venv-py312\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-wake.txt
if errorlevel 1 goto :failed
".venv-py312\Scripts\python.exe" -m assistant.wake_word
if errorlevel 1 goto :failed
echo Korean wake detection installed. Start LUNA and say Luna, then your command.
pause
exit /b 0
:failed
echo Wake setup failed. Check network access and the error above.
pause
exit /b 1
