@echo off
REM ====================================================================
REM Stop All Services - Centralize Data Centre
REM Double-click this file to stop all running services
REM ====================================================================

title Centralize Data Centre - Stopping Services

echo.
echo ====================================================================
echo   CENTRALIZE DATA CENTRE - STOPPING ALL SERVICES
echo ====================================================================
echo.

REM Change to script directory
cd /d "%~dp0"

echo [INFO] Stopping all Python processes related to this project...
echo.

REM Stop VPS Data Collector
taskkill /FI "WINDOWTITLE eq VPS Data Collector*" /F >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] VPS Data Collector stopped
) else (
    echo [INFO] VPS Data Collector was not running
)

REM Stop WebSocket Broadcaster
taskkill /FI "WINDOWTITLE eq WebSocket Broadcaster*" /F >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] WebSocket Broadcaster stopped
) else (
    echo [INFO] WebSocket Broadcaster was not running
)

REM Stop VPS Backup Sync
taskkill /FI "WINDOWTITLE eq VPS Backup Sync*" /F >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] VPS Backup Sync stopped
) else (
    echo [INFO] VPS Backup Sync was not running
)

REM Alternative: Stop all Python processes (use with caution)
REM echo.
REM echo [WARNING] This will stop ALL Python processes!
REM set /p confirm="Are you sure? (y/n): "
REM if /i "%confirm%"=="y" (
REM     taskkill /IM python.exe /F >nul 2>&1
REM     echo [OK] All Python processes stopped
REM )

echo.
echo ====================================================================
echo   ALL SERVICES STOPPED
echo ====================================================================
echo.
echo This window will close in 5 seconds...
timeout /t 5 /nobreak >nul

