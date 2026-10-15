@echo off
setlocal
cd /d "%~dp0"
set "LUNA_PYTHON=.venv-py312\Scripts\python.exe"
if not exist "%LUNA_PYTHON%" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
"%LUNA_PYTHON%" -c "import struct,sys; sys.exit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto :failed
"%LUNA_PYTHON%" -m pip install --disable-pip-version-check --only-binary=PyAudio -r requirements-build.txt
if errorlevel 1 goto :failed
"%LUNA_PYTHON%" -m PyInstaller --noconfirm luna.spec
if errorlevel 1 goto :failed
echo Build completed: dist\Luna\Luna.exe
echo Distribute the entire dist\Luna folder, including _internal.
pause
exit /b 0
:failed
echo Build failed. Review the output above.
pause
exit /b 1
