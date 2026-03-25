# PowerShell wrapper for NIFTY database sync
# Easier to run from Windows

param(
    [switch]$Force,
    [switch]$Auto
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonScript = Join-Path $ScriptDir "sync_nifty_db.py"

# Check if Python is available - try multiple methods
$pythonCmd = $null

# Method 1: Try 'py' launcher (Windows Python Launcher)
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $pythonCmd = "py"
    Write-Host "✅ Found Python launcher (py)" -ForegroundColor Green
}
else {
    # Method 2: Try 'python' command
    $pythonExe = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonExe) {
        $pythonCmd = "python"
        Write-Host "✅ Found Python (python)" -ForegroundColor Green
    }
    else {
        # Method 3: Try 'python3'
        $python3Exe = Get-Command python3 -ErrorAction SilentlyContinue
        if ($python3Exe) {
            $pythonCmd = "python3"
            Write-Host "✅ Found Python (python3)" -ForegroundColor Green
        }
    }
}

if (-not $pythonCmd) {
    Write-Host "❌ Python not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please install Python or ensure it's in your PATH." -ForegroundColor Yellow
    Write-Host "You can download Python from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Alternatively, run the script directly:" -ForegroundColor Yellow
    Write-Host "  py sync_nifty_db.py" -ForegroundColor Cyan
    if ($Force) {
        Write-Host "  py sync_nifty_db.py --force" -ForegroundColor Cyan
    }
    if ($Auto) {
        Write-Host "  py sync_nifty_db.py --auto" -ForegroundColor Cyan
    }
    exit 1
}

# Build command arguments
$scriptArgs = @()
if ($Force) {
    $scriptArgs += "--force"
}
if ($Auto) {
    $scriptArgs += "--auto"
}

# Run Python script
Write-Host "🚀 Starting sync script..." -ForegroundColor Cyan
Write-Host ""

try {
    if ($scriptArgs.Count -gt 0) {
        & $pythonCmd $PythonScript @scriptArgs
    } else {
        & $pythonCmd $PythonScript
    }
    
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
        Write-Host ""
        Write-Host "❌ Sync failed with exit code: $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}
catch {
    Write-Host ""
    Write-Host "❌ Error running sync script: $_" -ForegroundColor Red
    exit 1
}

