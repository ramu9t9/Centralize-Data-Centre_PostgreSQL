# PowerShell script to start the local real-time data centre
# Updated to the current architecture:
# - VPS collector writes to project-root/data/nifty_local.db
# - WebSocket broadcaster reads from that DB and serves ws://localhost:8765
# - Optional: Data sync service for VPS gap-fill utilities

param(
    [switch]$DataService,
    [switch]$SyncService,
    [switch]$Both
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Host "=" -NoNewline -ForegroundColor Cyan
Write-Host "=" * 59 -ForegroundColor Cyan
Write-Host "  LOCAL REAL-TIME DATA CENTRE" -ForegroundColor Green
Write-Host "=" -NoNewline -ForegroundColor Cyan
Write-Host "=" * 59 -ForegroundColor Cyan
Write-Host ""

# Check Python
$pythonCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} else {
    Write-Host "❌ Python not found!" -ForegroundColor Red
    exit 1
}

Write-Host "✅ Using Python: $pythonCmd" -ForegroundColor Green
Write-Host ""

# Start services
if ($Both -or (-not $DataService -and -not $SyncService)) {
    Write-Host "🚀 Starting both services..." -ForegroundColor Cyan
    Write-Host ""
    
    # Start broadcaster in new window
    Write-Host "📡 Starting WebSocket Broadcaster Service..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot'; & $pythonCmd services\websocket_broadcaster_service.py"
    
    Start-Sleep -Seconds 2
    
    # Start sync service in new window
    Write-Host "🔄 Starting Data Sync Service..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ProjectRoot'; & $pythonCmd services\data_sync_service.py"
    
    Write-Host ""
    Write-Host "✅ Both services started in separate windows" -ForegroundColor Green
}
elseif ($DataService) {
    Write-Host "📡 Starting WebSocket Broadcaster Service..." -ForegroundColor Yellow
    Write-Host ""
    Set-Location $ProjectRoot
    & $pythonCmd "services\websocket_broadcaster_service.py"
}
elseif ($SyncService) {
    Write-Host "🔄 Starting Data Sync Service..." -ForegroundColor Yellow
    Write-Host ""
    Set-Location $ProjectRoot
    & $pythonCmd "services\data_sync_service.py"
}

Write-Host ""
Write-Host "=" -NoNewline -ForegroundColor Cyan
Write-Host "=" * 59 -ForegroundColor Cyan
Write-Host "  Services are running!" -ForegroundColor Green
Write-Host "  WebSocket: ws://localhost:8765" -ForegroundColor Cyan
Write-Host "  Database: data/nifty_local.db" -ForegroundColor Cyan
Write-Host "=" -NoNewline -ForegroundColor Cyan
Write-Host "=" * 59 -ForegroundColor Cyan
