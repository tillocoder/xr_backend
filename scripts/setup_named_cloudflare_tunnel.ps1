param(
    [string]$TunnelName = "xrinvest-backend",
    [string]$Hostname = "api.xrinvest.uz",
    [string]$OriginUrl = "http://127.0.0.1:8000",
    [switch]$OverwriteDns,
    [switch]$InstallService
)

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

function Get-CloudflaredHome {
    $cloudflaredHome = Join-Path $env:USERPROFILE ".cloudflared"
    if (-not (Test-Path $cloudflaredHome)) {
        New-Item -ItemType Directory -Path $cloudflaredHome -Force | Out-Null
    }
    return $cloudflaredHome
}

function Get-OriginCertPath {
    $cloudflaredHome = Get-CloudflaredHome
    $certPath = Join-Path $cloudflaredHome "cert.pem"
    if (-not (Test-Path $certPath)) {
        throw "Cloudflare CLI login hali qilinmagan. Avval: cloudflared tunnel login"
    }
    return $certPath
}

function Get-TunnelIdFromText {
    param(
        [string]$Text
    )

    $match = [regex]::Match($Text, "\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
    if (-not $match.Success) {
        throw "Tunnel ID aniqlanmadi. Output: $Text"
    }
    return $match.Value
}

function Get-ExistingTunnel {
    param(
        [string]$Cloudflared,
        [string]$OriginCert,
        [string]$Name
    )

    $json = & $Cloudflared --origincert $OriginCert tunnel list -o json -n $Name 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Tunnel list bajarilmadi: $json"
    }
    $parsed = $json | ConvertFrom-Json
    if ($parsed -is [System.Array] -and $parsed.Count -gt 0) {
        return $parsed[0]
    }
    return $null
}

function Ensure-Tunnel {
    param(
        [string]$Cloudflared,
        [string]$OriginCert,
        [string]$Name
    )

    $existing = Get-ExistingTunnel -Cloudflared $Cloudflared -OriginCert $OriginCert -Name $Name
    if ($null -ne $existing) {
        return [pscustomobject]@{
            Id = [string]$existing.id
            Name = [string]$existing.name
            Created = $false
        }
    }

    $output = & $Cloudflared --origincert $OriginCert tunnel create $Name 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Tunnel create bajarilmadi: $output"
    }

    $tunnelId = Get-TunnelIdFromText -Text ($output | Out-String)
    return [pscustomobject]@{
        Id = $tunnelId
        Name = $Name
        Created = $true
    }
}

function Set-DnsRoute {
    param(
        [string]$Cloudflared,
        [string]$OriginCert,
        [string]$TunnelName,
        [string]$Hostname,
        [bool]$Overwrite
    )

    $args = @("--origincert", $OriginCert, "tunnel", "route", "dns")
    if ($Overwrite) {
        $args += "--overwrite-dns"
    }
    $args += @($TunnelName, $Hostname)

    $output = & $Cloudflared @args 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "DNS route yaratilmadi: $output"
    }
    return $output
}

function Write-TunnelConfig {
    param(
        [string]$TunnelId,
        [string]$Hostname,
        [string]$OriginUrl
    )

    $cloudflaredHome = Get-CloudflaredHome
    $credentialsPath = Join-Path $cloudflaredHome "$TunnelId.json"
    if (-not (Test-Path $credentialsPath)) {
        throw "Tunnel credential fayli topilmadi: $credentialsPath"
    }

    $configPath = Join-Path $cloudflaredHome "config.yml"
    $config = @"
tunnel: $TunnelId
credentials-file: '$credentialsPath'

ingress:
  - hostname: $Hostname
    service: $OriginUrl
  - service: http_status:404
"@

    Set-Content -LiteralPath $configPath -Value $config -Encoding UTF8
    return $configPath
}

$cloudflared = Get-CloudflaredCommand
$originCert = Get-OriginCertPath
$tunnel = Ensure-Tunnel -Cloudflared $cloudflared -OriginCert $originCert -Name $TunnelName
$dnsOutput = Set-DnsRoute -Cloudflared $cloudflared -OriginCert $originCert -TunnelName $TunnelName -Hostname $Hostname -Overwrite $OverwriteDns.IsPresent
$configPath = Write-TunnelConfig -TunnelId $tunnel.Id -Hostname $Hostname -OriginUrl $OriginUrl

Write-Host "Tunnel tayyor:"
Write-Host "  Name: $($tunnel.Name)"
Write-Host "  ID: $($tunnel.Id)"
Write-Host "  Hostname: $Hostname"
Write-Host "  Origin: $OriginUrl"
Write-Host "  Config: $configPath"
Write-Host ""
Write-Host ($dnsOutput | Out-String)

if ($InstallService) {
    Write-Host "cloudflared Windows service o'rnatilmoqda..."
    & $cloudflared service install
    if ($LASTEXITCODE -ne 0) {
        throw "cloudflared service install muvaffaqiyatsiz tugadi."
    }
    Write-Host "Windows service o'rnatildi."
} else {
    Write-Host "Tunnelni ishga tushirish uchun:"
    Write-Host "  cloudflared tunnel run $TunnelName"
}
