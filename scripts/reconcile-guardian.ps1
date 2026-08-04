# Reconcile orphan llama-guardian — run elevated via gsudo.
# Kills the orphan holding 8080, restarts PM2's llama-guardian, verifies.
# Safe during GPU benches: only touches port 8080 / PM2, never 8082 or llama-server.

$ErrorActionPreference = 'Stop'

Write-Host "=== STEP 1: Kill orphan PID 12776 ===" -ForegroundColor Cyan
try {
    Stop-Process -Id 12776 -Force -ErrorAction Stop
    Write-Host "Sent kill to PID 12776"
} catch {
    Write-Host "PID 12776 not found (already gone): $_"
}
Start-Sleep -Milliseconds 800

$conn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "PORT 8080 STILL LISTENING (pid=$($conn.OwningProcess))" -ForegroundColor Red
    exit 1
}
Write-Host "PORT 8080 FREE" -ForegroundColor Green

Write-Host "`n=== STEP 2: Restart PM2 llama-guardian ===" -ForegroundColor Cyan
# Skip jlist inspection — pm2 jlist emits duplicate env-var keys on Windows
# (COMMONPROGRAMFILES/CommonProgramFiles) that break PS 5.1's ConvertFrom-Json.
# `pm2 restart` handles online/errored/stopped uniformly.
Write-Host "Restarting llama-guardian..."
& pm2 restart llama-guardian 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host "restart returned non-zero — trying delete + start fresh"
    & pm2 delete llama-guardian 2>&1 | Out-Host
    & pm2 start C:\Workspace\Infrastructure\llama-cpp-server\ecosystem.config.cjs --only llama-guardian 2>&1 | Out-Host
}
Start-Sleep -Seconds 4

Write-Host "`n=== STEP 3: Verify ===" -ForegroundColor Cyan
$conn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    Write-Host "Port 8080 owned by PID $($conn.OwningProcess) ($($proc.ProcessName))" -ForegroundColor Green
} else {
    Write-Host "WARNING: port 8080 not listening yet" -ForegroundColor Yellow
}

# Health check (GET only — safe, does not wake model)
try {
    $h = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/__guardian/health' -TimeoutSec 5
    Write-Host "`nGuardian health: status=$($h.status) llama_up=$($h.llama_up) active_reqs=$($h.active_requests)" -ForegroundColor Green
    if ($h.llama_up -eq $true) {
        Write-Host "WARNING: llama_up is true — model was woken!" -ForegroundColor Red
    } else {
        Write-Host "Model still asleep (llama_up=false) — bench safe" -ForegroundColor Green
    }
} catch {
    Write-Host "Health check failed: $_" -ForegroundColor Yellow
}

# Confirm bench untouched
$bench = Get-NetTCPConnection -LocalPort 8082 -State Listen -ErrorAction SilentlyContinue
if ($bench) {
    Write-Host "`nBench (8082): PID $($bench.OwningProcess) still listening — untouched" -ForegroundColor Green
}

Write-Host "`n=== DONE ===" -ForegroundColor Cyan
