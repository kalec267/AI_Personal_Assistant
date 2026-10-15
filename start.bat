@echo off
cd /d "%~dp0"
if not exist ".venv-py312\Scripts\pythonw.exe" goto :missing_setup
".venv-py312\Scripts\python.exe" -c "import struct,sys; sys.exit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto :missing_setup
start "" ".venv-py312\Scripts\pythonw.exe" "launch.py"
exit /b 0
:missing_setup
echo Run setup.bat with Python 3.12 64-bit first.
pause
exit /b 1
