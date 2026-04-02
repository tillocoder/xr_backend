param(
    [string]$TunnelName = "xrinvest-backend",
    [string]$ListenHost = "0.0.0.0",
    [int]$Port = 8000,
    [int]$LandingPort = 8080,
    [int]$BackendReadyTimeoutSeconds = 45
)

function Test-TcpEndpoint {
    param(
        [string]$TargetHost,
        [int]$TargetPort
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $asyncResult = $client.BeginConnect($TargetHost, $TargetPort, $null, $null)
        $connected = $asyncResult.AsyncWaitHandle.WaitOne(300)
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

function Get-CloudflaredCommand {
    $command = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    foreach ($candidate in @(
        (Join-Path "${env:ProgramFiles(x86)}" "cloudflared\cloudflared.exe"),
        (Join-Path "$env:ProgramFiles" "cloudflared\cloudflared.exe"),
        (Join-Path "$env:LOCALAPPDATA" "Microsoft\WinGet\Links\cloudflared.exe")
    )) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "cloudflared topilmadi. O'rnatish uchun: winget install --id Cloudflare.cloudflared"
}

function Test-TunnelProcess {
    param(
        [string]$ExpectedTunnelName
    )

    $escapedName = [regex]::Escape($ExpectedTunnelName)
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" -ErrorAction SilentlyContinue
    foreach ($process in @($processes)) {
        $commandLine = [string]$process.CommandLine
        if ($commandLine -match "tunnel\s+run\s+$escapedName(\s|$)") {
            return $true
        }
    }
    return $false
}

function Start-DetachedPowerShellScript {
    param(
        [string]$ScriptPath,
        [string[]]$ArgumentList,
        [string]$StdOutPath,
        [string]$StdErrPath
    )

    $command = "& '$ScriptPath'"
    foreach ($argument in $ArgumentList) {
        $command += " $argument"
    }

    return Start-Process powershell `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdOutPath `
        -RedirectStandardError $StdErrPath `
        -PassThru
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $projectRoot "runtime-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$backendStdOut = Join-Path $logDir "backend-stdout.log"
$backendStdErr = Join-Path $logDir "backend-stderr.log"
$landingStdOut = Join-Path $logDir "landing-stdout.log"
$landingStdErr = Join-Path $logDir "landing-stderr.log"
$tunnelStdOut = Join-Path $logDir "cloudflared-stdout.log"
$tunnelStdErr = Join-Path $logDir "cloudflared-stderr.log"

$backendScript = Join-Path $projectRoot "scripts\start_backend_for_tunnel.ps1"
$landingRoot = Join-Path (Split-Path -Parent $projectRoot) "xrinvest-landing"
$landingScript = Join-Path $landingRoot "preview.ps1"

if ((Test-Path $landingRoot) -and (Test-Path $landingScript)) {
    $landingIsRunning = Test-TcpEndpoint -TargetHost "127.0.0.1" -TargetPort $LandingPort
    if ($landingIsRunning) {
        Write-Host "Landing page allaqachon ishlayapti: 127.0.0.1:$LandingPort"
    } else {
        $landingProcess = Start-DetachedPowerShellScript `
            -ScriptPath $landingScript `
            -ArgumentList @("-Port $LandingPort") `
            -StdOutPath $landingStdOut `
            -StdErrPath $landingStdErr
        Write-Host "Landing page ishga tushirildi. PID: $($landingProcess.Id)"
    }
} else {
    Write-Warning "Landing page topilmadi. Root domain uchun folder kutilgan joy: $landingRoot"
}

$backendIsRunning = Test-TcpEndpoint -TargetHost "127.0.0.1" -TargetPort $Port
if ($backendIsRunning) {
    Write-Host "Backend allaqachon ishlayapti: 127.0.0.1:$Port"
} else {
    $backendProcess = Start-DetachedPowerShellScript `
        -ScriptPath $backendScript `
        -ArgumentList @("-ListenHost '$ListenHost'", "-Port $Port") `
        -StdOutPath $backendStdOut `
        -StdErrPath $backendStdErr
    Write-Host "Backend ishga tushirildi. PID: $($backendProcess.Id)"

    $deadline = (Get-Date).AddSeconds([Math]::Max(10, $BackendReadyTimeoutSeconds))
    do {
        Start-Sleep -Seconds 2
        $backendIsRunning = Test-TcpEndpoint -TargetHost "127.0.0.1" -TargetPort $Port
    } until ($backendIsRunning -or (Get-Date) -ge $deadline)

    if (-not $backendIsRunning) {
        Write-Warning "Backend hali 127.0.0.1:$Port da tayyor emas. Tunnel baribir ishga tushiriladi."
    }
}

$cloudflared = Get-CloudflaredCommand
if (Test-TunnelProcess -ExpectedTunnelName $TunnelName) {
    Write-Host "Cloudflare named tunnel allaqachon ishlayapti: $TunnelName"
} else {
    $tunnelProcess = Start-Process $cloudflared `
        -ArgumentList @("tunnel", "run", $TunnelName) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $tunnelStdOut `
        -RedirectStandardError $tunnelStdErr `
        -PassThru
    Write-Host "Cloudflare named tunnel ishga tushirildi. PID: $($tunnelProcess.Id)"
}

Write-Host "Launcher yakunlandi."
Write-Host "Logs:"
Write-Host "  $landingStdOut"
Write-Host "  $landingStdErr"
Write-Host "  $backendStdOut"
Write-Host "  $backendStdErr"
Write-Host "  $tunnelStdOut"
Write-Host "  $tunnelStdErr"
