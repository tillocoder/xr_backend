param(
    [string]$TaskName = "XRInvestBackendStack",
    [string]$TunnelName = "xrinvest-backend",
    [int]$Port = 8000
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$launcherPath = Join-Path $projectRoot "scripts\start_backend_stack.ps1"
if (-not (Test-Path $launcherPath)) {
    throw "Launcher script topilmadi: $launcherPath"
}

function Install-StartupCmdFallback {
    param(
        [string]$RootPath,
        [string]$Launcher,
        [string]$ExpectedTunnelName,
        [int]$ListenPort
    )

    $startupDir = [Environment]::GetFolderPath("Startup")
    $startupCmdPath = Join-Path $startupDir "XRInvestBackendStack.cmd"
    $command = @"
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '$RootPath'; & '$Launcher' -TunnelName '$ExpectedTunnelName' -Port $ListenPort"
"@
    Set-Content -LiteralPath $startupCmdPath -Value $command -Encoding ASCII
    return $startupCmdPath
}

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-Command", "& '$launcherPath' -TunnelName '$TunnelName' -Port $Port"
)

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($arguments -join " ")
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Starts XR Invest landing page, backend, and Cloudflare named tunnel at logon." `
        -ErrorAction Stop `
        -Force | Out-Null

    Write-Host "Task yaratildi: $TaskName"
    Write-Host "Launcher: $launcherPath"
    Write-Host "Tunnel: $TunnelName"
    Write-Host "Port: $Port"
} catch {
    $startupCmdPath = Install-StartupCmdFallback `
        -RootPath $projectRoot `
        -Launcher $launcherPath `
        -ExpectedTunnelName $TunnelName `
        -ListenPort $Port
    Write-Warning "Task Scheduler ruxsat bermadi. Startup fallback yozildi."
    Write-Host "Startup launcher: $startupCmdPath"
}
