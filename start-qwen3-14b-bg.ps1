# start-qwen3-14b-bg.ps1
# Background launcher — starts llama-server hidden, logs to qwen3-14b.log.
# Safe to run on login; exits immediately after spawning the server process.

$serverExe = "C:\llama-cpp-server\llama-server.exe"
$modelFile = "C:\llama-cpp-server\models\Qwen_Qwen3-14B-Q4_K_L.gguf"
$logFile   = "C:\llama-cpp-server\qwen3-14b.log"

# Already running? Skip.
if (Get-Process -Name llama-server -ErrorAction SilentlyContinue) {
    Write-Host "llama-server already running — skipping."
    exit 0
}

$argList = "--model `"$modelFile`" --host 0.0.0.0 --port 8080 --alias qwen3-14b --gpu-layers 99 --ctx-size 16384 --parallel 1 --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on --jinja --batch-size 4096 --ubatch-size 512 --cont-batching --metrics --reasoning off"

Start-Process -FilePath $serverExe `
    -ArgumentList $argList `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError "$logFile.err" `
    -WindowStyle Hidden

Write-Host "llama-server started in background. Log: $logFile"
