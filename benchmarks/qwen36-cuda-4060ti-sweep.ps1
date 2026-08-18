# Qwen3.6-35B-A3B UD-IQ3_XXS tuning sweep — CUDA b10488, RTX 4060 Ti 16GB (X870E)
# 2026-08-18. Methodology follows nemotron-tuning-sweep.py: bench the flag
# matrix first, then walk the context ladder to find max stable ctx.
# Phase A: llama-bench matrix — ubatch x KV-cache-type, pp2048 + tg128 at depth 0 and 8192
# Phase B: llama-server context ladder with the winning KV type, VRAM headroom recorded
$ErrorActionPreference = 'Continue'
$build = 'C:\Workspace\Infrastructure\llama-cpp-server-cuda-b10488'
$model = 'C:\Workspace\Infrastructure\llama-cpp-server\models\Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf'
$outDir = 'C:\Workspace\Infrastructure\llama-cpp-server\benchmarks'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$json = "$outDir\qwen36-cuda-4060ti-sweep-$stamp.json"

Write-Host "=== Phase A: llama-bench matrix ==="
& "$build\llama-bench.exe" -m $model -ngl 99 -fa 1 `
  -ub 512,1024,2048 -b 2048 -ctk f16 -ctv f16 `
  -p 2048 -n 128 -d 0,8192 -r 2 -o json > "$json.f16"
& "$build\llama-bench.exe" -m $model -ngl 99 -fa 1 `
  -ub 512,1024,2048 -b 2048 -ctk q8_0 -ctv q8_0 `
  -p 2048 -n 128 -d 0,8192 -r 2 -o json > "$json.q8"
Write-Host "bench outputs: $json.f16 / $json.q8"

Write-Host "=== Phase B: context ladder (q8_0 KV) ==="
foreach ($ctx in 65536, 98304, 131072) {
  $p = Start-Process -PassThru -WindowStyle Hidden "$build\llama-server.exe" -ArgumentList `
    '--model', $model, '--host', '127.0.0.1', '--port', '8083', '--gpu-layers', '99', `
    '--ctx-size', "$ctx", '--flash-attn', 'on', '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0', `
    '--parallel', '1', '--jinja'
  $up = $false
  foreach ($i in 1..40) {
    Start-Sleep -Seconds 3
    try { if ((Invoke-RestMethod http://127.0.0.1:8083/health -TimeoutSec 2).status -eq 'ok') { $up = $true; break } } catch {}
    if ($p.HasExited) { break }
  }
  if ($up) {
    $vram = (& nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader) -join ''
    Write-Host ("ctx {0,7}: LOADED   VRAM {1}" -f $ctx, $vram)
  } else {
    Write-Host ("ctx {0,7}: FAILED (OOM or crash)" -f $ctx)
  }
  if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
  Start-Sleep -Seconds 5
}
Write-Host '=== SWEEP DONE ==='
