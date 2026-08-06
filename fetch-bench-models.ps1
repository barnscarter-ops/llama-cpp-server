# Downloads the R9700 benchmark candidate models.
# All three fit in 32GB VRAM with room for KV cache. Safe to re-run:
# `hf download` skips files already present and resumes partial ones.

$ErrorActionPreference = "Continue"
$MODELS = "C:\Workspace\Infrastructure\llama-cpp-server\models"

# repo, file, approx GB, why it's in the lineup
$targets = @(
  @{ repo = "unsloth/Qwen3.6-35B-A3B-MTP-GGUF"; file = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"; gb = 21.1
     why  = "Direct upgrade of the current IQ3_XXS (12.3GB). Same model, ~4.5 bits/weight instead of ~3. Plus MTP tensors." }
  @{ repo = "unsloth/Qwen3.6-35B-A3B-MTP-GGUF"; file = "Qwen3.6-35B-A3B-UD-Q5_K_M.gguf"; gb = 25.2
     why  = "Same again at ~5.5 bits. Tests how far quant can be pushed before 32GB stops holding a useful context." }
  @{ repo = "unsloth/Qwen3.6-27B-MTP-GGUF";     file = "Qwen3.6-27B-UD-Q5_K_XL.gguf";    gb = 19.0
     why  = "Dense 27B, reported 77.2% SWE-bench Verified. Dense = slower per token than the A3B MoE but higher ceiling." }
)

$total = ($targets | Measure-Object -Property gb -Sum).Sum
Write-Host "Fetching $($targets.Count) models, ~$total GB total, into $MODELS" -ForegroundColor Cyan
Write-Host ""

foreach ($t in $targets) {
  $dest = Join-Path $MODELS $t.file
  if (Test-Path $dest) {
    Write-Host "SKIP  $($t.file) — already present" -ForegroundColor Green
    continue
  }
  Write-Host "GET   $($t.file)  (~$($t.gb) GB)" -ForegroundColor Yellow
  Write-Host "      $($t.why)" -ForegroundColor DarkGray
  hf download $t.repo $t.file --local-dir $MODELS
  if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL  $($t.file) exited $LASTEXITCODE — re-run this script to resume" -ForegroundColor Red
  } else {
    Write-Host "OK    $($t.file)" -ForegroundColor Green
  }
  Write-Host ""
}

Write-Host "=== models on disk ===" -ForegroundColor Cyan
Get-ChildItem $MODELS -Filter *.gguf |
  Select-Object Name, @{n='GB'; e={ [math]::Round($_.Length/1GB, 2) }} |
  Format-Table -AutoSize

# NOT downloaded, deliberately:
#   Qwen3-Coder-Next — smallest GGUF quant is ~45GB, past the R9700's 32GB.
#     Would need substantial CPU offload, which makes it useless as a
#     GPU benchmark. Revisit only if a smaller quant ships.
