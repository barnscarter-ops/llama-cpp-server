# Start the tuned Qwen3.6-35B-A3B llama-server.
# Uses PM2 if available; falls back to the direct llama-server invocation.

$ErrorActionPreference = "Stop"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$ROOT = Split-Path -Parent $SCRIPT_DIR
$ECO = "$ROOT\ecosystem.config.cjs"
$PM2_APP = "qwen3-llama"
$MODEL = "$ROOT\models\Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf"

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-Path $MODEL)) {
    throw "Model file not found: $MODEL"
}

if ((Test-Command "pm2") -and (Test-Path $ECO)) {
    Write-Host "Starting via PM2 ($PM2_APP)..." -ForegroundColor Cyan

    $pm2List = pm2 jlist 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
    $app = $pm2List | Where-Object { $_.name -eq $PM2_APP }

    if (-not $app) {
        pm2 start $ECO | Out-Null
    }

    pm2 start $PM2_APP | Out-Null
    pm2 logs $PM2_APP
} else {
    Write-Host "PM2 not available; starting llama-server directly..." -ForegroundColor Yellow

    & "$ROOT\llama-server.exe" `
        --model $MODEL `
        --host 127.0.0.1 --port 8081 `
        --alias qwen3.6-35b `
        --gpu-layers 99 `
        --ctx-size 65536 `
        --parallel 1 `
        --cache-type-k q4_0 --cache-type-v q4_0 `
        --jinja `
        --batch-size 2048 --ubatch-size 512 `
        --cont-batching `
        --spec-type draft-mtp --spec-draft-n-max 2 `
        --reasoning off
}
