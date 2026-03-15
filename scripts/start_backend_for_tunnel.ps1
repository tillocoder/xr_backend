param(
    [string]$ListenHost = "0.0.0.0",
    [int]$Port = 8000
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$env:XR_BACKEND_HOST = $ListenHost
$env:XR_BACKEND_PORT = "$Port"

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

& $python (Join-Path $projectRoot "run_dev.py")
