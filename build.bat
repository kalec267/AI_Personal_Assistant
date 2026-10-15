@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm luna.spec
if errorlevel 1 goto :failed
echo Build completed: dist\Luna\Luna.exe
echo Distribute the entire dist\Luna folder, including _internal.
pause
exit /b 0
:failed
echo Build failed. Review the output above.
pause
exit /b 1
