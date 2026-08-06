# Benchmarks every GGUF in models\ and prints a comparison table.
#
# Typical use on the R9700 (find the index with --list-devices first):
#   .\bench-models.ps1 -Backend vulkan -Device 1
#
# Compare against the old card:
#   .\bench-models.ps1 -Backend cuda
#
# Results are appended to benchmarks\model-comparison.md so runs accumulate.

[CmdletBinding()]
param(
  [ValidateSet("vulkan", "cuda")] [string] $Backend = "vulkan",
  [string] $Device,                       # Vulkan device index; omit to use all
  [int]    $Prompt  = 512,                # prefill tokens  (pp)
  [int]    $Gen     = 128,                # generated tokens (tg)
  [int]    $Reps    = 3,
  [string] $Filter                        # optional substring to bench a subset
)

$ErrorActionPreference = "Stop"
$ROOT   = Split-Path -Parent $MyInvocation.MyCommand.Path
$MODELS = Join-Path $ROOT "models"
$OUTDIR = Join-Path $ROOT "benchmarks"

$binDir = if ($Backend -eq "vulkan") { "C:\Workspace\Infrastructure\llama-cpp-server-vulkan" } else { $ROOT }
$bench  = Join-Path $binDir "llama-bench.exe"
if (-not (Test-Path $bench)) { throw "llama-bench.exe not found for backend '$Backend' at $bench" }

if ($Device) { $env:GGML_VK_VISIBLE_DEVICES = $Device }

$gguf = Get-ChildItem $MODELS -Filter *.gguf | Where-Object { $_.Name -notmatch '-\d{5}-of-\d{5}\.gguf$' }
if ($Filter) { $gguf = $gguf | Where-Object { $_.Name -like "*$Filter*" } }
if (-not $gguf) { throw "No GGUF models matched in $MODELS" }

Write-Host "Backend : $Backend" -ForegroundColor Cyan
Write-Host "Device  : $(if ($Device) { $Device } else { '(all)' })" -ForegroundColor Cyan
Write-Host "Models  : $($gguf.Count)`n" -ForegroundColor Cyan

$rows = @()
foreach ($m in $gguf) {
  $sizeGb = [math]::Round($m.Length / 1GB, 2)
  Write-Host "==> $($m.Name)  ($sizeGb GB)" -ForegroundColor Yellow

  # -o json so we parse numbers instead of scraping the pretty table.
  $raw = & $bench -m $m.FullName -ngl 99 -fa 1 -r $Reps -p $Prompt -n $Gen -o json 2>&1
  $json = ($raw | Where-Object { $_ -notmatch '^(load_backend|ggml_|build:)' }) -join "`n"

  try {
    $parsed = $json | ConvertFrom-Json
  } catch {
    Write-Host "    FAILED to parse output — model likely did not fit in VRAM" -ForegroundColor Red
    $rows += [pscustomobject]@{ Model = $m.Name; SizeGB = $sizeGb; Prefill = "OOM/err"; Generate = "OOM/err" }
    continue
  }

  $pp = ($parsed | Where-Object { $_.n_prompt -gt 0 } | Select-Object -First 1).avg_ts
  $tg = ($parsed | Where-Object { $_.n_gen    -gt 0 } | Select-Object -First 1).avg_ts

  $rows += [pscustomobject]@{
    Model    = $m.Name
    SizeGB   = $sizeGb
    Prefill  = [math]::Round($pp, 1)
    Generate = [math]::Round($tg, 1)
  }
  Write-Host ("    pp{0} {1,8} t/s   tg{2} {3,7} t/s" -f $Prompt, [math]::Round($pp,1), $Gen, [math]::Round($tg,1)) -ForegroundColor Green
}

Write-Host "`n=== Results ($Backend) ===" -ForegroundColor Cyan
$rows | Sort-Object -Property Generate -Descending | Format-Table -AutoSize

New-Item -ItemType Directory -Force -Path $OUTDIR | Out-Null
$out = Join-Path $OUTDIR "model-comparison.md"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"

$md = @()
$md += ""
$md += "## $stamp — $Backend backend, pp$Prompt / tg$Gen, r=$Reps"
$md += ""
$md += "| Model | Size (GB) | Prefill t/s | Generate t/s |"
$md += "| --- | ---: | ---: | ---: |"
foreach ($r in ($rows | Sort-Object -Property Generate -Descending)) {
  $md += "| $($r.Model) | $($r.SizeGB) | $($r.Prefill) | $($r.Generate) |"
}
$md += ""
Add-Content -Path $out -Value ($md -join "`n")

Write-Host "Appended to $out" -ForegroundColor Gray
Write-Host ""
Write-Host "NOTE: this measures speed only, not answer quality. A model that wins" -ForegroundColor DarkGray
Write-Host "on t/s can still be the worse choice. Check quality separately before" -ForegroundColor DarkGray
Write-Host "switching production over." -ForegroundColor DarkGray
