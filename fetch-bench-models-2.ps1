# Round 2: the non-Qwen field.
#
# Every repo_id and filename here was verified against the HuggingFace blob API
# on 2026-08-04 — none are guessed. Sizes are decimal GB, as HF reports them.
# The 32 GiB R9700 is ~34.4 decimal GB, so a 23 GB weight file still leaves
# ~11 GB for KV cache. Safe to re-run: `hf download` skips and resumes.

$ErrorActionPreference = "Continue"
$MODELS = "C:\Workspace\Infrastructure\llama-cpp-server\models"

$targets = @(
  @{ repo = "unsloth/GLM-4.7-Flash-GGUF"; file = "GLM-4.7-Flash-UD-Q4_K_XL.gguf"; gb = 17.52
     why  = "GLM-4.7-Flash is the only GLM that fits — GLM-5.x is 216GB+ at IQ1. Strong agentic reputation." }

  @{ repo = "unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF"; file = "Devstral-Small-2-24B-Instruct-2512-UD-Q4_K_XL.gguf"; gb = 14.51
     why  = "Mistral's purpose-built coding-agent model. Small enough to run at Q6/Q8 later if Q4 shows promise." }

  @{ repo = "unsloth/Nemotron-3-Nano-30B-A3B-GGUF"; file = "Nemotron-3-Nano-30B-A3B-UD-Q4_K_XL.gguf"; gb = 22.83
     why  = "NVIDIA's 30B MoE, 3B active — same A3B shape as the current Qwen, so a clean architectural comparison." }

  @{ repo = "ggml-org/gpt-oss-20b-GGUF"; file = "gpt-oss-20b-MXFP4.gguf"; gb = 12.11
     why  = "OpenAI's open model. Natively MXFP4 — this IS the full-precision release, not a lossy quant." }

  @{ repo = "unsloth/Seed-OSS-36B-Instruct-GGUF"; file = "Seed-OSS-36B-Instruct-UD-Q4_K_XL.gguf"; gb = 22.03
     why  = "ByteDance dense 36B. Dense at this size is the closest thing to a quality ceiling that still fits." }

  @{ repo = "LGAI-EXAONE/EXAONE-4.5-33B-GGUF"; file = "EXAONE-4.5-33B-Q4_K_M.gguf"; gb = 20.05
     why  = "LG's dense 33B, first-party GGUF. Under-benchmarked publicly, which makes it worth measuring here." }

  @{ repo = "unsloth/gemma-4-31B-it-GGUF"; file = "gemma-4-31B-it-UD-Q4_K_XL.gguf"; gb = 18.82
     why  = "Google's dense 31B. Gemma has historically punched above its size on instruction following." }

  @{ repo = "unsloth/granite-4.0-h-small-GGUF"; file = "granite-4.0-h-small-UD-Q4_K_XL.gguf"; gb = 18.76
     why  = "IBM's 32B hybrid. Apache-2.0 and built for tool use — the enterprise-agent angle." }
)

$total = [math]::Round(($targets | Measure-Object -Property gb -Sum).Sum, 1)
Write-Host "Fetching $($targets.Count) non-Qwen models, ~$total GB total" -ForegroundColor Cyan
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
    Write-Host "FAIL  $($t.file) exited $LASTEXITCODE — re-run to resume" -ForegroundColor Red
  } else {
    Write-Host "OK    $($t.file)" -ForegroundColor Green
  }
  Write-Host ""
}

Write-Host "=== models on disk ===" -ForegroundColor Cyan
Get-ChildItem $MODELS -Filter *.gguf |
  Select-Object Name, @{n='GB'; e={ [math]::Round($_.Length/1GB, 2) }} |
  Sort-Object GB | Format-Table -AutoSize

# Ruled out by verified size, not by guesswork:
#   gpt-oss-120b            63.4 GB   (MXFP4 native; no smaller form exists)
#   Nemotron-3-Super-120B   52.7 GB   at IQ1_M
#   DeepSeek-V4-Flash       82.5 GB   at IQ1_S
#   GLM-5.2                216.7 GB   at IQ1_S
#   MiniMax-M3             128.4 GB   at IQ1_M
#   Kimi-K2.6 / K3         340 / 594 GB
# Also: Meta Llama 5 does not exist as of 2026-08-04 — newest is Llama 4.
