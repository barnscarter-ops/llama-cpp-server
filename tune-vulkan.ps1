# Re-runs the flag tuning sweep against the Vulkan backend.
#
# The flags baked into ecosystem.config.cjs (ubatch 1024, flash-attn auto,
# f16 KV) were tuned for CUDA on a 16GB 4060 Ti. None of that carries over to
# a different backend on a different vendor's card — this re-derives them.
#
# Run AFTER picking a model from bench-models.ps1:
#   .\tune-vulkan.ps1 -Model models\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf -Device 1
#
# Takes a while (each combination reloads the model). -Quick halves the matrix.

[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $Model,
  [string] $Device,
  [int]    $Ctx   = 32768,   # sweep at a realistic working context, not 512
  [int]    $Reps  = 3,
  [switch] $Quick
)

$ErrorActionPreference = "Stop"
$ROOT  = Split-Path -Parent $MyInvocation.MyCommand.Path
$bench = "C:\Workspace\Infrastructure\llama-cpp-server-vulkan\llama-bench.exe"
if (-not (Test-Path $bench)) { throw "Vulkan llama-bench.exe not found at $bench" }

if (-not [System.IO.Path]::IsPathRooted($Model)) { $Model = Join-Path $ROOT $Model }
if (-not (Test-Path $Model)) { throw "Model not found: $Model" }
if ($Device) { $env:GGML_VK_VISIBLE_DEVICES = $Device }

# Each entry is one configuration to measure. Keep the list short — every row
# is a full model load plus $Reps passes.
$configs = @(
  @{ name = "baseline (ub512, fa on, f16 kv)"; args = @("-ub","512","-fa","1") }
  @{ name = "ub1024, fa on, f16 kv";           args = @("-ub","1024","-fa","1") }
  @{ name = "ub2048, fa on, f16 kv";           args = @("-ub","2048","-fa","1") }
  @{ name = "ub1024, fa OFF, f16 kv";          args = @("-ub","1024","-fa","0") }
)
if (-not $Quick) {
  $configs += @(
    @{ name = "ub1024, fa on, q8_0 kv"; args = @("-ub","1024","-fa","1","-ctk","q8_0","-ctv","q8_0") }
    @{ name = "ub1024, fa on, q4_0 kv"; args = @("-ub","1024","-fa","1","-ctk","q4_0","-ctv","q4_0") }
    @{ name = "ub2048, fa on, q8_0 kv"; args = @("-ub","2048","-fa","1","-ctk","q8_0","-ctv","q8_0") }
  )
}

Write-Host "Model  : $(Split-Path -Leaf $Model)" -ForegroundColor Cyan
Write-Host "Device : $(if ($Device) { $Device } else { '(all)' })" -ForegroundColor Cyan
Write-Host "Context: $Ctx  (prefill measured at this depth, not 512)" -ForegroundColor Cyan
Write-Host "Configs: $($configs.Count)`n" -ForegroundColor Cyan

$rows = @()
foreach ($c in $configs) {
  Write-Host "==> $($c.name)" -ForegroundColor Yellow

  # -d $Ctx puts $Ctx tokens of KV in place before measuring, so these numbers
  # reflect a loaded agent conversation rather than an empty one.
  $raw = & $bench -m $Model -ngl 99 -r $Reps -p 512 -n 128 -d $Ctx @($c.args) -o json 2>&1
  $json = ($raw | Where-Object { $_ -notmatch '^(load_backend|ggml_|build:)' }) -join "`n"

  try { $parsed = $json | ConvertFrom-Json } catch {
    Write-Host "    FAILED — likely out of VRAM at this context" -ForegroundColor Red
    $rows += [pscustomobject]@{ Config = $c.name; Prefill = "OOM/err"; Generate = "OOM/err" }
    continue
  }

  $pp = ($parsed | Where-Object { $_.n_prompt -gt 0 } | Select-Object -First 1).avg_ts
  $tg = ($parsed | Where-Object { $_.n_gen    -gt 0 } | Select-Object -First 1).avg_ts
  $rows += [pscustomobject]@{
    Config   = $c.name
    Prefill  = [math]::Round($pp, 1)
    Generate = [math]::Round($tg, 1)
  }
  Write-Host ("    pp {0,8} t/s   tg {1,7} t/s" -f [math]::Round($pp,1), [math]::Round($tg,1)) -ForegroundColor Green
}

Write-Host "`n=== Vulkan tuning sweep @ ${Ctx} ctx ===" -ForegroundColor Cyan
$rows | Format-Table -AutoSize

$OUTDIR = Join-Path $ROOT "benchmarks"
New-Item -ItemType Directory -Force -Path $OUTDIR | Out-Null
$out = Join-Path $OUTDIR "vulkan-tuning-sweep.md"

$md = @("", "## $(Get-Date -Format 'yyyy-MM-dd HH:mm') — $(Split-Path -Leaf $Model) @ $Ctx ctx", "",
        "| Config | Prefill t/s | Generate t/s |", "| --- | ---: | ---: |")
foreach ($r in $rows) { $md += "| $($r.Config) | $($r.Prefill) | $($r.Generate) |" }
$md += ""
Add-Content -Path $out -Value ($md -join "`n")

Write-Host "Appended to $out" -ForegroundColor Gray
Write-Host "Apply the winner to ecosystem.config.cjs (qwen3-llama-vulkan block)." -ForegroundColor Gray
