@echo off
setlocal
cd /d "%~dp0"
set "LUNA_PYTHON=.venv-py312\Scripts\python.exe"
if not exist "%LUNA_PYTHON%" (
    py -3.12 -c "import struct,sys; sys.exit(0 if struct.calcsize('P') == 8 else 1)" >nul 2>&1
    if errorlevel 1 goto :missing_python
    py -3.12 -m venv .venv-py312
    if errorlevel 1 goto :failed
)
"%LUNA_PYTHON%" -c "import struct,sys; sys.exit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto :wrong_environment
"%LUNA_PYTHON%" -m pip install --disable-pip-version-check --only-binary=PyAudio -r requirements.txt
if errorlevel 1 goto :failed
echo Setup completed. Run start.bat to open LUNA.
if /I not "%~1"=="--no-pause" pause
exit /b 0
:missing_python
echo Python 3.12 64-bit is required for microphone package support.
echo Install Windows installer (64-bit) from https://www.python.org/downloads/release/python-31210/
echo Include Python Launcher and Tcl/Tk in the installation.
echo Python 3.14 may remain installed. Then run setup.bat again.
if /I not "%~1"=="--no-pause" pause
exit /b 1
:wrong_environment
echo The existing .venv-py312 folder does not use Python 3.12 64-bit.
echo Rename that folder to preserve it, then run setup.bat again.
if /I not "%~1"=="--no-pause" pause
exit /b 1
:failed
echo Setup failed. Check the error above and network access.
echo Existing .venv folders and calendar data have not been removed.
if /I not "%~1"=="--no-pause" pause
exit /b 1
