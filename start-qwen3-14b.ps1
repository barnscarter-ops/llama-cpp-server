$ErrorActionPreference = 'Stop'
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$serverExe = "C:\llama-cpp-server\llama-server.exe"
$modelFile = "C:\llama-cpp-server\models\Qwen_Qwen3-14B-Q4_K_L.gguf"
$logFile   = "C:\llama-cpp-server\qwen3-14b.log"

if (-not (Test-Path $serverExe)) { Write-Error "llama-server.exe not found at $serverExe" }
if (-not (Test-Path $modelFile)) { Write-Error "Model not found at $modelFile" }

Write-Host "Starting Qwen3-14B Q6_K (100% GPU) on http://0.0.0.0:8080/v1 ..." -ForegroundColor Cyan
Write-Host "Log: $logFile" -ForegroundColor DarkGray

$args = @(
    '--model',        $modelFile,
    '--host',         '0.0.0.0',
    '--port',         '8080',
    '--alias',        'qwen3-14b',
    '--gpu-layers',   '99',          # all 40 layers on GPU, zero CPU math
    '--ctx-size',     '16384',
    '--parallel',     '1',
    '--cache-type-k', 'q8_0',
    '--cache-type-v', 'q8_0',
    '--flash-attn',   'on',
    '--jinja',                        # uses model's native Qwen3 tool-call template
    '--batch-size',   '4096',
    '--ubatch-size',  '512',
    '--cont-batching',
    '--metrics',
    '--reasoning',    'off'
)

$quotedArgs = $args | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
}

cmd.exe /c "`"$serverExe`" $($quotedArgs -join ' ') >> `"$logFile`" 2>&1"
