param(
    [string]$ListenHost = "0.0.0.0",
    [int]$Port = 8000
)

function Test-TcpEndpoint {
    param(
        [string]$TargetHost,
        [int]$Port
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $asyncResult = $client.BeginConnect($TargetHost, $Port, $null, $null)
        $connected = $asyncResult.AsyncWaitHandle.WaitOne(250)
        if (-not $connected) {
            return $false
        }
        $client.EndConnect($asyncResult)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$env:XR_BACKEND_HOST = $ListenHost
$env:XR_BACKEND_PORT = "$Port"
$env:XR_PROCESS_WORKER_COUNT = "1"
$env:XR_REDIS_REQUIRED_FOR_RUNTIME = "false"

$python = $null
foreach ($candidate in @(".venv\\Scripts\\python.exe", "venv\\Scripts\\python.exe")) {
    $fullPath = Join-Path $projectRoot $candidate
    if (Test-Path $fullPath) {
        $python = $fullPath
        break
    }
}

if (-not $python) {
    $python = "python"
}

$requirements = Join-Path $projectRoot "requirements.txt"
if (Test-Path $requirements) {
    & $python -c "import feedparser, httpx" *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing backend dependencies from requirements.txt..."
        & $python -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) {
            throw "pip install failed. Fix dependencies then re-run."
        }
    }
}

try {
    Write-Host "Applying database migrations (alembic upgrade head)..."
    Push-Location $projectRoot
    & $python -m alembic upgrade head
} catch {
    Write-Host "Warning: alembic migration failed. Backend will start, but news/AI config tables may be missing."
} finally {
    Pop-Location
}

if (-not $env:XR_REDIS_URL) {
    $env:XR_REDIS_URL = "redis://127.0.0.1:6379/0"
}

$redisUri = [System.Uri]$env:XR_REDIS_URL
$redisHost = if ([string]::IsNullOrWhiteSpace($redisUri.Host)) { "127.0.0.1" } else { $redisUri.Host }
$redisPort = if ($redisUri.Port -gt 0) { $redisUri.Port } else { 6379 }
if (-not (Test-TcpEndpoint -TargetHost $redisHost -Port $redisPort)) {
    Write-Host "Redis is not reachable at ${redisHost}:${redisPort}. Starting backend in single-process best-effort mode for local tunnel use."
    Write-Host "Realtime fanout across multiple backend instances will stay disabled until Redis is available."
}

& $python (Join-Path $projectRoot "run_dev.py")
