param(
    [string]$TaskName = "XRInvestBackendStack"
)

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "Task o'chirildi: $TaskName"
} catch {
    Write-Warning "Task topilmadi yoki o'chirishga ruxsat yo'q: $TaskName"
}

$startupDir = [Environment]::GetFolderPath("Startup")
$startupCmdPath = Join-Path $startupDir "XRInvestBackendStack.cmd"
if (Test-Path $startupCmdPath) {
    Remove-Item -LiteralPath $startupCmdPath -Force
    Write-Host "Startup launcher o'chirildi: $startupCmdPath"
}
