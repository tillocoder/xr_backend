param(
    [string]$Domain = "api.xrinvest.uz",
    [switch]$RestoreDhcp
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ActiveInterfaceAliases {
    $configs = Get-NetIPConfiguration | Where-Object {
        $_.NetAdapter.Status -eq "Up" -and ($_.IPv4DefaultGateway -or $_.IPv6DefaultGateway)
    }

    return $configs | Select-Object -ExpandProperty InterfaceAlias -Unique
}

function Invoke-Netsh {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & netsh @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "netsh failed: $($Arguments -join ' ')`n$output"
    }

    return $output
}

if (-not (Test-IsAdmin)) {
    $argList = @(
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-Domain", "`"$Domain`""
    )

    if ($RestoreDhcp) {
        $argList += "-RestoreDhcp"
    }

    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argList | Out-Null
    exit 0
}

$aliases = Get-ActiveInterfaceAliases
if (-not $aliases) {
    throw "No active network adapter with a default gateway was found."
}

foreach ($alias in $aliases) {
    if ($RestoreDhcp) {
        Invoke-Netsh @("interface", "ipv4", "set", "dnsservers", "name=$alias", "source=dhcp") | Out-Null
        Invoke-Netsh @("interface", "ipv6", "set", "dnsservers", "name=$alias", "source=dhcp") | Out-Null
        continue
    }

    Invoke-Netsh @("interface", "ipv4", "set", "dnsservers", "name=$alias", "static", "1.1.1.1", "primary") | Out-Null
    Invoke-Netsh @("interface", "ipv4", "add", "dnsservers", "name=$alias", "8.8.8.8", "index=2") | Out-Null
    Invoke-Netsh @("interface", "ipv6", "set", "dnsservers", "name=$alias", "static", "2606:4700:4700::1111", "primary") | Out-Null
    Invoke-Netsh @("interface", "ipv6", "add", "dnsservers", "name=$alias", "2001:4860:4860::8888", "index=2") | Out-Null
}

Clear-DnsClientCache
ipconfig /flushdns | Out-Null

Write-Host ""
if ($RestoreDhcp) {
    Write-Host "DNS settings were restored to automatic (DHCP)." -ForegroundColor Green
} else {
    Write-Host "DNS settings were updated to Cloudflare + Google public resolvers." -ForegroundColor Green
}

Write-Host ""
Write-Host "Active adapters:" -ForegroundColor Cyan
Get-DnsClientServerAddress -AddressFamily IPv4 |
    Where-Object { $_.InterfaceAlias -in $aliases } |
    Format-Table -AutoSize

Write-Host ""
Write-Host "DNS lookup check:" -ForegroundColor Cyan
nslookup $Domain

Write-Host ""
Write-Host "HTTPS health check:" -ForegroundColor Cyan
try {
    $response = Invoke-WebRequest "https://$Domain/health" -UseBasicParsing -TimeoutSec 20
    $response.Content
} catch {
    $_ | Out-String
}

Write-Host ""
Write-Host "Done. If your browser was already open, reload the page." -ForegroundColor Yellow
