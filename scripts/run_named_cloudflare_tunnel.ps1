param(
    [string]$TunnelName = "xrinvest-backend"
)

$command = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $command) {
    throw "cloudflared topilmadi. O'rnatish uchun: winget install --id Cloudflare.cloudflared"
}

Write-Host "Named tunnel ishga tushmoqda: $TunnelName"
& $command.Source tunnel run $TunnelName
