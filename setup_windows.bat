@echo off
echo ============================================
echo   C Test Runner - Setup for Windows
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.8+ from https://python.org
    pause & exit /b 1
)
echo [OK] Python found.

:: Check pip and install nothing (stdlib only - tkinter is built-in)
python -c "import tkinter" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] tkinter not found. Re-install Python and check "tcl/tk" option.
    pause & exit /b 1
)
echo [OK] tkinter found.

:: Check gcc
gcc --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] gcc not found in PATH.
    echo    Please install one of:
    echo      - MinGW-w64: https://winlibs.com  (recommended)
    echo      - MSYS2:     https://www.msys2.org
    echo      - TDM-GCC:   https://jmeubank.github.io/tdm-gcc
    echo    Then add gcc to your PATH and re-run this script.
    echo.
) else (
    echo [OK] gcc found.
)

echo.
echo ============================================
echo   All checks done. Run the app with:
echo     python c_test_runner37.py
echo ============================================
echo.

:: Launch directly
python c_test_runner37.py
pause
