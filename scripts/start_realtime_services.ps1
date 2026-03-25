# PowerShell script to start all real-time services
# This starts the WebSocket Broadcaster Service
# Note: VPS Data Collector should be started separately

Write-Host "=" -NoNewline
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "REAL-TIME DATA CENTRE SERVICES" -ForegroundColor Green
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host ""

# Change to script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
$projectRoot = Split-Path -Parent $scriptDir

Write-Host "📁 Working directory: $scriptDir" -ForegroundColor Yellow
Write-Host ""

# Check if Python is available
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✅ Python found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ Python not found! Please install Python." -ForegroundColor Red
    exit 1
}

# Check if VPS collector is running
Write-Host "🔍 Checking if VPS Data Collector is running..." -ForegroundColor Yellow
$vpsRunning = $false
try {
    # Use the dedicated checker (CommandLine is not exposed on Get-Process by default)
    powershell -NoProfile -ExecutionPolicy Bypass -File "$scriptDir\check_vps_process.ps1" > $null
    if ($LASTEXITCODE -eq 0) { $vpsRunning = $true }
} catch {
    $vpsRunning = $false
}

if ($vpsRunning) {
    Write-Host "✅ VPS Data Collector is running" -ForegroundColor Green
} else {
    Write-Host "⚠️  VPS Data Collector is NOT running" -ForegroundColor Yellow
    Write-Host "   Start it with: cd vps_system; py nifty_stream_local_sqlite.py" -ForegroundColor Yellow
    Write-Host ""
    $continue = Read-Host "Continue anyway? (y/n)"
    if ($continue -ne "y") {
        exit 0
    }
}
Write-Host ""

# Check if database exists
$dbPath = Join-Path $projectRoot "data\nifty_local.db"
if (Test-Path $dbPath) {
    Write-Host "✅ Database found: $dbPath" -ForegroundColor Green
} else {
    Write-Host "⚠️  Database not found: $dbPath" -ForegroundColor Yellow
    Write-Host "   Make sure VPS Data Collector is running to create the database" -ForegroundColor Yellow
    Write-Host ""
    $continue = Read-Host "Continue anyway? (y/n)"
    if ($continue -ne "y") {
        exit 0
    }
}
Write-Host ""

# Check if WebSocket broadcaster is already running
Write-Host "🔍 Checking if WebSocket Broadcaster is already running..." -ForegroundColor Yellow
$broadcasterRunning = $null
try {
    $broadcasterRunning = Get-Process python -ErrorAction SilentlyContinue | Where-Object {
        $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
        $cmd -and $cmd -like "*websocket_broadcaster_service.py*"
    }
} catch {
    $broadcasterRunning = $null
}

if ($broadcasterRunning) {
    Write-Host "⚠️  WebSocket Broadcaster is already running" -ForegroundColor Yellow
    Write-Host "   Process ID: $($broadcasterRunning.Id)" -ForegroundColor Yellow
    Write-Host ""
    $restart = Read-Host "Stop and restart? (y/n)"
    if ($restart -eq "y") {
        Stop-Process -Id $broadcasterRunning.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Write-Host "✅ Stopped existing service" -ForegroundColor Green
    } else {
        Write-Host "Keeping existing service running" -ForegroundColor Yellow
        exit 0
    }
}
Write-Host ""

# Start WebSocket Broadcaster Service
Write-Host "🚀 Starting WebSocket Broadcaster Service..." -ForegroundColor Green
Write-Host ""

Start-Process py -ArgumentList "$projectRoot\services\websocket_broadcaster_service.py" -WindowStyle Normal

Write-Host "✅ WebSocket Broadcaster Service started!" -ForegroundColor Green
Write-Host ""
Write-Host "📊 Service Status:" -ForegroundColor Cyan
Write-Host "   - WebSocket: ws://localhost:8765" -ForegroundColor White
Write-Host "   - Database: data\nifty_local.db" -ForegroundColor White
Write-Host "   - Monitor Interval: 5 seconds" -ForegroundColor White
Write-Host ""
Write-Host "🧪 Test the service:" -ForegroundColor Cyan
Write-Host "   py scripts\test_websocket_client.py" -ForegroundColor White
Write-Host ""
Write-Host "📝 Logs:" -ForegroundColor Cyan
Write-Host "   data\broadcaster_service.log" -ForegroundColor White
Write-Host ""
Write-Host "Press any key to exit..." -ForegroundColor Yellow
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

