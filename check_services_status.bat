@echo off
setlocal enabledelayedexpansion
REM ====================================================================
REM Check Services Status - Centralize Data Centre
REM Double-click this file to check if all services are running
REM ====================================================================

title Centralize Data Centre - Service Status

echo.
echo ====================================================================
echo   CENTRALIZE DATA CENTRE - SERVICE STATUS
echo ====================================================================
echo.

REM Change to script directory
cd /d "%~dp0"

echo Checking services...
echo.

REM Check VPS Data Collector
set VPS_RUNNING=0
REM Check by window title (set in start_all_services.bat)
tasklist /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq VPS Data Collector*" 2>NUL | find /I "python.exe" >NUL
if %ERRORLEVEL% EQU 0 (
    echo [OK] VPS Data Collector: RUNNING
    set VPS_RUNNING=1
) else (
    REM Fallback: Check if any Python process is running nifty_stream script using PowerShell
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\check_vps_process.ps1" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [OK] VPS Data Collector: RUNNING ^(detected by script name^)
        set VPS_RUNNING=1
    ) else (
        echo [X] VPS Data Collector: NOT RUNNING
    )
)

REM Check WebSocket Broadcaster
set WS_RUNNING=0
netstat -an 2>nul | findstr /C:":8765" | findstr /C:"LISTENING" >nul 2>&1
set WS_PORT_CHECK=%ERRORLEVEL%
if !WS_PORT_CHECK! EQU 0 (
    echo [OK] WebSocket Broadcaster: RUNNING ^(Port 8765 is listening^)
    set WS_RUNNING=1
) else (
    echo [X] WebSocket Broadcaster: NOT RUNNING ^(Port 8765 not listening^)
)

REM Check database
set DB_EXISTS=0
if exist "data\nifty_local.db" (
    set DB_EXISTS=1
    for %%A in ("data\nifty_local.db") do set DB_SIZE=%%~zA
    if !DB_SIZE! GTR 0 (
        echo [OK] Database: EXISTS (data\nifty_local.db^) - Size: !DB_SIZE! bytes
    ) else (
        echo [WARNING] Database: EXISTS but EMPTY or CORRUPTED (data\nifty_local.db^)
    )
) else (
    echo [X] Database: NOT FOUND (data\nifty_local.db^)
    echo [INFO] Database will be created when VPS collector starts
)

echo.
echo ====================================================================
echo   STATUS CHECK COMPLETE
echo ====================================================================
echo.
echo To start all services, run: start_all_services.bat
echo.
pause

