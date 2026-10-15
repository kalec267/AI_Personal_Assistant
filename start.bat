@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "launch.py"
    exit /b
)
pyw -3 "launch.py"
if errorlevel 1 (
    echo Install Python 3.12 with Python Launcher and Tcl/Tk, then run setup.bat.
    pause
)
