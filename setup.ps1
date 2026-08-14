# llama-cpp-server setup for Orca
# Ensures the configured local llama-server is running via PM2.

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$ECO = "$ROOT\ecosystem.config.cjs"
$PM2_APP = "local-llm"
$LLAMA_PORT = 8081
$HEALTH_URL = "http://127.0.0.1:$LLAMA_PORT/v1/models"
$EXPECTED_ALIAS = "local-llm"

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "OK: $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "WARN: $Message" -ForegroundColor Yellow
}

# ── 1. Validate environment ─────────────────────────────────────────────────
Write-Step "Checking environment"

if (-not (Test-Path $ECO)) {
    throw "PM2 ecosystem config not found: $ECO"
}

if (-not (Test-Command "pm2")) {
    Write-Warn "pm2 not found in PATH. Install it with: npm install -g pm2"
    throw "pm2 is required to manage the llama-server process."
}

if (-not (Test-Command "node")) {
    throw "node is required but not found in PATH."
}

Write-Ok "pm2 and node are available"

# ── 2. Ensure PM2 app is registered ─────────────────────────────────────────
Write-Step "Checking PM2 registration for '$PM2_APP'"

$pm2List = pm2 jlist 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
$app = $pm2List | Where-Object { $_.name -eq $PM2_APP }

if (-not $app) {
    Write-Warn "'$PM2_APP' is not registered in PM2. Registering from $ECO"
    pm2 start $ECO | Out-Null

    # The ecosystem config registers local-llm as STOPPED, so we must start it.
    pm2 start $PM2_APP | Out-Null
} else {
    Write-Ok "'$PM2_APP' is registered (status: $($app.pm2_env.status))"
}

# ── 3. Ensure the process is running ────────────────────────────────────────
Write-Step "Checking if '$PM2_APP' is running"

$pm2List = pm2 jlist 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
$app = $pm2List | Where-Object { $_.name -eq $PM2_APP }

if ($app.pm2_env.status -ne "online") {
    Write-Warn "'$PM2_APP' is $($app.pm2_env.status). Starting now..."
    pm2 start $PM2_APP | Out-Null
} else {
    Write-Ok "'$PM2_APP' is online"
}

# ── 4. Verify the tuned model is loaded ─────────────────────────────────────
Write-Step "Verifying loaded model at $HEALTH_URL"

$modelOk = $false
$deadline = (Get-Date).AddSeconds(120)

while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-RestMethod -Uri $HEALTH_URL -Method GET -TimeoutSec 5 -ErrorAction Stop
        $alias = $resp.data.id
        if ($alias -eq $EXPECTED_ALIAS) {
            $modelOk = $true
            break
        }
        Write-Warn "Model mismatch: expected '$EXPECTED_ALIAS', got '$alias'"
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $modelOk) {
    throw "llama-server did not report model '$EXPECTED_ALIAS' within 120 seconds. Check logs: pm2 logs $PM2_APP"
}

Write-Ok "Model '$EXPECTED_ALIAS' is loaded and serving on port $LLAMA_PORT"

# ── 5. Persist PM2 startup (optional, best-effort) ──────────────────────────
Write-Step "Ensuring PM2 startup is saved"
pm2 save --force 2>$null | Out-Null

Write-Host ""
Write-Host "Setup complete. The local model server is ready." -ForegroundColor Green
Write-Host "  Health:   $HEALTH_URL" -ForegroundColor Gray
Write-Host "  Logs:     pm2 logs $PM2_APP" -ForegroundColor Gray
Write-Host "  Stop:     pm2 stop $PM2_APP" -ForegroundColor Gray
