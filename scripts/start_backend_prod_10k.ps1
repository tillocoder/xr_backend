$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot

function Get-BackendPython {
    foreach ($candidate in @(".venv\\Scripts\\python.exe", "venv\\Scripts\\python.exe")) {
        $fullPath = Join-Path $projectRoot $candidate
        if (Test-Path $fullPath) {
            return $fullPath
        }
    }
    return "python"
}

$python = Get-BackendPython

$env:XR_REDIS_REQUIRED_FOR_RUNTIME = "true"
$env:XR_BACKEND_EXPECTED_PEAK_WS_CONNECTIONS = "10000"
$env:XR_BACKEND_TARGET_WS_PER_WORKER = "2500"
$env:XR_BACKEND_BACKLOG = "8192"
$env:XR_BACKEND_LIMIT_CONCURRENCY = "24000"
$env:XR_WEBSOCKET_MAX_PENDING_MESSAGES_PER_CONNECTION = "32"
$env:XR_BACKEND_WS_MAX_QUEUE = "64"
$env:XR_BACKEND_WS_PER_MESSAGE_DEFLATE = "false"

Write-Host "Starting XR backend with 10k realtime preset..."
& $python (Join-Path $projectRoot "run_prod.py")
