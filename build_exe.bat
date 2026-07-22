@echo off
chcp 65001 >nul 2>&1
echo ============================================
echo   PCB Assembly Studio - Build EXE
echo ============================================
echo.

REM Find Python
set PYTHON_CMD=
where python >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=python
    goto :found
)
where py >nul 2>&1
if not errorlevel 1 (
    set PYTHON_CMD=py
    goto :found
)
echo ERROR: Python not found!
echo Install from https://python.org/downloads/
pause
exit /b 1

:found
echo Found Python:
%PYTHON_CMD% --version
echo.

echo [1/3] Installing dependencies...
%PYTHON_CMD% -m pip install --upgrade pip
%PYTHON_CMD% -m pip install pyinstaller matplotlib openpyxl
echo.

REM Add Python Scripts to PATH for this session
for /f "delims=" %%i in ('%PYTHON_CMD% -c "import sysconfig; print(sysconfig.get_path(\"scripts\"))"') do set SCRIPTS_DIR=%%i
echo Adding to PATH: %SCRIPTS_DIR%
set PATH=%SCRIPTS_DIR%;%PATH%

echo.
echo [2/3] Building EXE (takes 1-2 minutes)...
%PYTHON_CMD% -m PyInstaller --onefile --windowed --name "PCBAssemblyStudio" pcb_assembly_studio.py

echo.
if exist "dist\PCBAssemblyStudio.exe" (
    echo ============================================
    echo   DONE!
    echo ============================================
    echo.
    echo EXE: dist\PCBAssemblyStudio.exe
    echo Copy it anywhere and run - no Python needed.
    echo.
    explorer dist
) else (
    echo.
    echo Build with PyInstaller failed.
    echo Trying alternative method...
    echo.
    pyinstaller --onefile --windowed --name "PCBAssemblyStudio" pcb_assembly_studio.py
    if exist "dist\PCBAssemblyStudio.exe" (
        echo DONE! EXE: dist\PCBAssemblyStudio.exe
        explorer dist
    ) else (
        echo.
        echo ============================================
        echo   BUILD FAILED
        echo ============================================
        echo.
        echo You can still run the app directly:
        echo   python pcb_assembly_studio.py
        echo.
        echo Or try building manually:
        echo   pip install pyinstaller
        echo   pyinstaller --onefile --windowed --name PCBAssemblyStudio pcb_assembly_studio.py
    )
)

echo.
pause
