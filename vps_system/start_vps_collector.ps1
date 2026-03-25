# PowerShell script to start VPS data collector (exact copy)
# This runs the exact same code as VPS, just locally

Write-Host "=" -NoNewline
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "VPS DATA COLLECTOR - LOCAL COPY" -ForegroundColor Green
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host ""

# Change to script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

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

# Check if required packages are installed
Write-Host "🔍 Checking dependencies..." -ForegroundColor Yellow
$required = @("pandas", "requests", "sqlalchemy", "SmartApi", "pyotp")
$missing = @()

foreach ($pkg in $required) {
    $check = python -c "import $pkg" 2>&1
    if ($LASTEXITCODE -ne 0) {
        $missing += $pkg
    }
}

if ($missing.Count -gt 0) {
    Write-Host "⚠️  Missing packages: $($missing -join ', ')" -ForegroundColor Yellow
    Write-Host "📦 Installing requirements..." -ForegroundColor Yellow
    pip install -r requirements.txt
} else {
    Write-Host "✅ All dependencies installed" -ForegroundColor Green
}

Write-Host ""
Write-Host "🚀 Starting VPS data collector..." -ForegroundColor Green
Write-Host ""

# Run the collector
python nifty_stream_local_sqlite.py

