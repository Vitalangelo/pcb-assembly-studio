@echo off
chcp 65001 >nul 2>&1
title PCB Assembly Studio
echo ============================================
echo   PCB Assembly Studio
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
echo ERROR: Python not found.
echo.
echo Install it from https://python.org/downloads/
echo During setup, tick "Add Python to PATH" on the first screen.
echo.
pause
exit /b 1

:found
echo Using Python:
%PYTHON_CMD% --version
echo.

REM Install the required packages only if they are missing
%PYTHON_CMD% -c "import matplotlib, gerbonara" >nul 2>&1
if errorlevel 1 (
    echo First run - installing matplotlib and gerbonara.
    echo This takes a minute or two. Only happens once.
    echo.
    %PYTHON_CMD% -m pip install matplotlib gerbonara
    echo.
    %PYTHON_CMD% -c "import matplotlib, gerbonara" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo ============================================
        echo   Could not install the dependencies.
        echo ============================================
        echo.
        echo Try running this by hand to see the error:
        echo   %PYTHON_CMD% -m pip install matplotlib gerbonara
        echo.
        pause
        exit /b 1
    )
)

echo Starting...
echo.
REM %~dp0 = this file's folder, so the app starts from anywhere
%PYTHON_CMD% "%~dp0pcb_assembly_studio.py"

if errorlevel 1 (
    echo.
    echo ============================================
    echo   The app closed with an error.
    echo ============================================
    echo.
    echo Copy the message above when reporting a bug:
    echo   https://github.com/VitaliyaF/pcb-assembly-studio/issues
    echo.
    pause
)
