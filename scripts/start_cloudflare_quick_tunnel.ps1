param(
    [string]$LocalUrl = "http://127.0.0.1:8000"
)

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    foreach ($candidate in @(
        (Join-Path "${env:ProgramFiles(x86)}" "cloudflared\\cloudflared.exe"),
        (Join-Path "$env:ProgramFiles" "cloudflared\\cloudflared.exe"),
        (Join-Path "$env:LOCALAPPDATA" "Microsoft\\WinGet\\Links\\cloudflared.exe")
    )) {
        if (Test-Path $candidate) {
            $cloudflared = @{ Source = $candidate }
            break
        }
    }
}

if (-not $cloudflared) {
    Write-Error "cloudflared topilmadi. O'rnatish uchun: winget install --id Cloudflare.cloudflared"
    exit 1
}

Write-Host "Cloudflare Quick Tunnel ishga tushmoqda: $LocalUrl"
Write-Host "Chiqqan https://...trycloudflare.com URL ni ilovadagi backend base URL qilib ishlating."

& $cloudflared.Source tunnel --url $LocalUrl
