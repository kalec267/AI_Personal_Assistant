@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-py312\Scripts\python.exe" (
    echo Run setup.bat with Python 3.12 64-bit first.
    pause
    exit /b 1
)
".venv-py312\Scripts\python.exe" -m pip install --disable-pip-version-check --only-binary=:all: -r requirements-voice.txt
if errorlevel 1 goto :failed
".venv-py312\Scripts\python.exe" -m assistant.local_voice
if errorlevel 1 goto :failed
echo Offline Korean female voice installed. Restart LUNA and choose local:F1.
pause
exit /b 0
:failed
echo Local voice setup failed. Review the message above. No model verification was bypassed.
pause
exit /b 1
