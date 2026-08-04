# Remove stale customer-chat-server PM2 artifacts (migrated to AIWA).
$ErrorActionPreference = 'SilentlyContinue'

$paths = @(
    'C:\ProgramData\pm2\pids\customer-chat-server-*.pid',
    'C:\ProgramData\pm2\logs\customer-chat-server-*.log'
)

foreach ($p in $paths) {
    $files = Get-ChildItem $p -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        Remove-Item $f.FullName -Force
        Write-Host "Removed: $($f.FullName)"
    }
}

# Verify nothing remains
$check = Get-ChildItem 'C:\ProgramData\pm2\pids\customer-chat-server-*','C:\ProgramData\pm2\logs\customer-chat-server-*' -ErrorAction SilentlyContinue
if ($null -eq $check) {
    Write-Host "All customer-chat-server artifacts removed" -ForegroundColor Green
} else {
    Write-Host "REMAINING:" -ForegroundColor Yellow
    $check | ForEach-Object { Write-Host $_.FullName }
}
