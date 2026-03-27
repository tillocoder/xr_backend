param(
    [ValidateSet("smoke", "1k", "5k", "10k", "custom")]
    [string]$Scenario = "smoke",
    [string]$Url = "ws://127.0.0.1:8000/api/v1/ws",
    [string]$MetricsUrl = "http://127.0.0.1:8000/metrics",
    [int]$Connections = 0,
    [int]$DurationSeconds = 0,
    [int]$RampSeconds = 0,
    [int]$ConnectConcurrency = 0,
    [int]$PingIntervalSeconds = 20,
    [double]$ConnectTimeoutSeconds = 10,
    [double]$CloseTimeoutSeconds = 5,
    [int]$PresenceRing = -1,
    [string]$TokenFile = "",
    [string]$UserIdPrefix = "load-user-",
    [string]$OutputDir = "",
    [switch]$DemoUserIdAuth,
    [switch]$FeedSubscribe,
    [switch]$InsecureSsl
)

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot

function Get-BackendPython {
    foreach ($candidate in @(".venv\Scripts\python.exe", "venv\Scripts\python.exe")) {
        $fullPath = Join-Path $projectRoot $candidate
        if (Test-Path $fullPath) {
            return $fullPath
        }
    }
    return "python"
}

function Get-ScenarioPreset {
    param([string]$Name)

    switch ($Name) {
        "smoke" {
            return @{
                Connections = 100
                DurationSeconds = 60
                RampSeconds = 10
                ConnectConcurrency = 5
                PresenceRing = 1
                FeedSubscribe = $true
            }
        }
        "1k" {
            return @{
                Connections = 1000
                DurationSeconds = 180
                RampSeconds = 60
                ConnectConcurrency = 50
                PresenceRing = 2
                FeedSubscribe = $true
            }
        }
        "5k" {
            return @{
                Connections = 5000
                DurationSeconds = 300
                RampSeconds = 180
                ConnectConcurrency = 150
                PresenceRing = 1
                FeedSubscribe = $true
            }
        }
        "10k" {
            return @{
                Connections = 10000
                DurationSeconds = 420
                RampSeconds = 300
                ConnectConcurrency = 250
                PresenceRing = 1
                FeedSubscribe = $true
            }
        }
        default {
            return @{
                Connections = 1000
                DurationSeconds = 180
                RampSeconds = 60
                ConnectConcurrency = 200
                PresenceRing = 1
                FeedSubscribe = $false
            }
        }
    }
}

$python = Get-BackendPython
$requirements = Join-Path $projectRoot "requirements.txt"
if (Test-Path $requirements) {
    & $python -c "import httpx, websockets" *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing load-test dependencies from requirements.txt..."
        & $python -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) {
            throw "pip install failed. Fix dependencies then re-run."
        }
    }
}

$preset = Get-ScenarioPreset -Name $Scenario
if ($Connections -le 0) { $Connections = [int]$preset.Connections }
if ($DurationSeconds -le 0) { $DurationSeconds = [int]$preset.DurationSeconds }
if ($RampSeconds -lt 0) { $RampSeconds = 0 }
elseif ($RampSeconds -eq 0) { $RampSeconds = [int]$preset.RampSeconds }
if ($ConnectConcurrency -le 0) { $ConnectConcurrency = [int]$preset.ConnectConcurrency }
if ($PresenceRing -lt 0) { $PresenceRing = [int]$preset.PresenceRing }
if (-not $FeedSubscribe.IsPresent -and [bool]$preset.FeedSubscribe) {
    $FeedSubscribe = $true
}

if (-not $DemoUserIdAuth.IsPresent -and [string]::IsNullOrWhiteSpace($TokenFile)) {
    $DemoUserIdAuth = $true
}

if ($DemoUserIdAuth.IsPresent) {
    Write-Host "Reminder: XR_ALLOW_INSECURE_DEMO_WS_USER_ID_AUTH=true faqat staging/load-test env uchun yoqilsin."
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $projectRoot "load-test-results"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$summaryFile = Join-Path $OutputDir "ws-$Scenario-$timestamp.json"

$arguments = @(
    (Join-Path $scriptRoot "ws_load_test.py")
    "--url", $Url
    "--connections", "$Connections"
    "--duration-seconds", "$DurationSeconds"
    "--ramp-seconds", "$RampSeconds"
    "--connect-concurrency", "$ConnectConcurrency"
    "--ping-interval-seconds", "$PingIntervalSeconds"
    "--connect-timeout-seconds", "$ConnectTimeoutSeconds"
    "--close-timeout-seconds", "$CloseTimeoutSeconds"
    "--presence-ring", "$PresenceRing"
    "--user-id-prefix", $UserIdPrefix
    "--summary-file", $summaryFile
)

if (-not [string]::IsNullOrWhiteSpace($MetricsUrl)) {
    $arguments += @("--metrics-url", $MetricsUrl)
}
if ($DemoUserIdAuth.IsPresent) {
    $arguments += "--demo-user-id-auth"
}
if (-not [string]::IsNullOrWhiteSpace($TokenFile)) {
    $arguments += @("--token-file", $TokenFile)
}
if ($FeedSubscribe.IsPresent) {
    $arguments += "--feed-subscribe"
}
if ($InsecureSsl.IsPresent) {
    $arguments += "--insecure-ssl"
}

Write-Host "Running websocket load test scenario '$Scenario'..."
Write-Host "Connections=$Connections DurationSeconds=$DurationSeconds RampSeconds=$RampSeconds ConnectConcurrency=$ConnectConcurrency PresenceRing=$PresenceRing"

& $python @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -eq 0) {
    Write-Host "Load test completed. Summary: $summaryFile"
}
exit $exitCode
