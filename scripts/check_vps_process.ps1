# PowerShell script to check if VPS Data Collector is running
# Returns exit code 0 if running, 1 if not

$proc = Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
    $cmd -and $cmd -like '*nifty_stream_local_sqlite.py*'
}

if ($proc) {
    exit 0
} else {
    exit 1
}

