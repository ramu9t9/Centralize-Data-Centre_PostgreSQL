@echo off
REM ====================================================================
REM Start All Services - Centralize Data Centre
REM Double-click this file to start all required services
REM ====================================================================

title Centralize Data Centre - Starting Services

echo.
echo ====================================================================
echo   CENTRALIZE DATA CENTRE - STARTING ALL SERVICES
echo ====================================================================
echo.

REM Change to script directory
cd /d "%~dp0"

REM Check if Python is available (try both 'py' and 'python')
set PYTHON_CMD=
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=py
    echo [OK] Python found ^(using 'py' launcher^)
    goto :python_found
)

where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set PYTHON_CMD=python
    echo [OK] Python found ^(using 'python' command^)
    goto :python_found
)

REM If we get here, Python was not found
echo [ERROR] Python not found!
echo.
echo Please install Python or ensure it's in your PATH.
echo You can download Python from: https://www.python.org/downloads/
echo.
pause
exit /b 1

:python_found
echo.

REM Check if database directory exists
if not exist "data\" (
    echo [INFO] Creating data directory...
    mkdir data
)

REM Check if VPS collector is already running
tasklist /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq VPS Data Collector*" 2>NUL | find /I /N "python.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [WARNING] VPS Data Collector appears to be already running
    echo.
) else (
    echo [1/3] Starting VPS Data Collector...
    start "VPS Data Collector" /MIN %PYTHON_CMD% "vps_system\nifty_stream_local_sqlite.py"
    timeout /t 3 /nobreak >nul
    echo [OK] VPS Data Collector started
    echo.
)

REM Check for GUI mode argument
set GUI_MODE=0
if "%1"=="--gui" set GUI_MODE=1
if "%1"=="-g" set GUI_MODE=1

REM Check if WebSocket broadcaster is already running
netstat -an | findstr ":8765" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] WebSocket Broadcaster appears to be already running on port 8765
    echo [INFO] Skipping startup (if not working, stop and restart)
    echo.
) else (
    
    if %GUI_MODE% EQU 1 (
        echo [2/3] Starting WebSocket Broadcaster Service with GUI...
        start "Broadcast Control Panel" %PYTHON_CMD% "services\start_broadcast_service.py"
        timeout /t 3 /nobreak >nul
        echo [OK] WebSocket Broadcaster with GUI started
        echo [INFO] GUI window should be visible - if not, check for errors in the window
    ) else (
        echo [2/3] Starting WebSocket Broadcaster Service (headless mode)...
        start "WebSocket Broadcaster" /MIN %PYTHON_CMD% "services\websocket_broadcaster_service.py"
        timeout /t 5 /nobreak >nul
        REM Verify it started
        netstat -an | findstr ":8765" >nul 2>&1
        if %ERRORLEVEL% EQU 0 (
            echo [OK] WebSocket Broadcaster started and listening on port 8765
        ) else (
            echo [WARNING] WebSocket Broadcaster may not have started properly
            echo [INFO] Check the minimized window for errors
        )
    )
    echo.
)

REM Optional: Start data sync service (commented out by default)
REM echo [3/3] Starting VPS Backup Sync Service...
REM start "VPS Backup Sync" /MIN py "services\data_sync_service.py"
REM timeout /t 2 /nobreak >nul
REM echo [OK] VPS Backup Sync started
REM echo.

echo ====================================================================
echo   ALL SERVICES STARTED SUCCESSFULLY!
echo ====================================================================
echo.
echo Services running:
echo   - VPS Data Collector (collecting NIFTY data every 5 seconds)
if %GUI_MODE% EQU 1 (
    echo   - WebSocket Broadcaster with GUI (broadcasting on ws://localhost:8765)
    echo   - Use the GUI window to control Live/Replay modes
) else (
    echo   - WebSocket Broadcaster (broadcasting on ws://localhost:8765)
    echo   - Run with --gui flag to start with control panel
)
echo.
echo To verify services are running:
echo   1. Check Task Manager for Python processes
echo   2. Run: %PYTHON_CMD% scripts\test_websocket_client.py
echo   3. Run: %PYTHON_CMD% scripts\verify_data_broadcasting.py
echo.
echo To start with GUI control panel:
echo   start_all_services.bat --gui
echo.
echo To stop services:
echo   - Close the Python windows, or
echo   - Use Task Manager to end Python processes
echo.
echo This window will close in 10 seconds...
timeout /t 10 /nobreak >nul

