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

& $python (Join-Path $projectRoot "run_dev.py")
