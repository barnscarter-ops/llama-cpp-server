# verify-kit.ps1 — re-check the X870E USB kit against its manifest.
# Safe to run anywhere: on this PC, or on the USB stick after copying.
# Read-only. Exits 0 if the staged core is intact, 1 otherwise.

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$expected = @(
  @{ Path='02-chipset\amd_software_8.07.16.1035.exe'
     Size=81490200
     Hash='1B55DD2DD661D19C5EA4D49BD53B673783E673DB9E427B709D404BB1BAE66BDB'
     Signer='Advanced Micro Devices' }
  @{ Path='03-gpu\595.97-desktop-win10-win11-64bit-international-dch-whql.exe'
     Size=957358592
     Hash='979ED00FEA181C786F608967377D6D83AC82E6368275994A4182EC79D97B3122'
     Signer='NVIDIA Corporation' }
  @{ Path='05-storage\Samsung_Magician_Installer_Official_9.0.1.950.exe'
     Size=204608280
     Hash='D46CF61AA5B5073138E9D063E91F1861776315A14DFCA3FFD8724611C21588D8'
     Signer='Samsung Electronics Co.' }
  @{ Path='06-tools\MediaCreationTool_Win11.exe'
     Size=21591048
     Hash='E887DFFF70BAF09A8C1DEBFE8C304DD9F2D9652FAE8B7C83B3C24554A79BBD7F'
     Signer='Microsoft Corporation' }
)

Write-Host "`nX870E USB kit verification"
Write-Host "root: $root`n"

$fail = 0
foreach ($e in $expected) {
    $full = Join-Path $root $e.Path
    $name = Split-Path $e.Path -Leaf

    if (-not (Test-Path $full)) {
        Write-Host ("  MISSING  {0}" -f $name) -ForegroundColor Red
        $fail++
        continue
    }

    $f = Get-Item $full
    if ($f.Length -ne $e.Size) {
        Write-Host ("  BAD SIZE {0}  got {1} expected {2}" -f $name, $f.Length, $e.Size) -ForegroundColor Red
        $fail++
        continue
    }

    $h = (Get-FileHash $full -Algorithm SHA256).Hash
    if ($h -ne $e.Hash) {
        Write-Host ("  BAD HASH {0}" -f $name) -ForegroundColor Red
        Write-Host ("           got {0}" -f $h) -ForegroundColor Red
        $fail++
        continue
    }

    $sig = Get-AuthenticodeSignature $full
    if ($sig.Status -ne 'Valid') {
        Write-Host ("  UNSIGNED {0}  status={1}" -f $name, $sig.Status) -ForegroundColor Red
        $fail++
        continue
    }

    Write-Host ("  OK       {0}  ({1} MB, {2})" -f $name, [math]::Round($f.Length/1MB,1), $e.Signer) -ForegroundColor Green
}

# The browser-download items: report presence, don't fail on absence.
Write-Host "`nBrowser-download items (see 01-BIOS\DOWNLOAD-THESE-IN-A-BROWSER.md):"
foreach ($d in @('01-BIOS','04-network')) {
    $dir = Join-Path $root $d
    $files = @(Get-ChildItem $dir -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -ne '.md' })
    if ($files.Count -eq 0) {
        Write-Host ("  EMPTY    {0}  - still needs a browser download" -f $d) -ForegroundColor Yellow
    } else {
        Write-Host ("  {0} file(s) in {1}:" -f $files.Count, $d) -ForegroundColor Green
        $files | ForEach-Object {
            $s = Get-AuthenticodeSignature $_.FullName
            Write-Host ("    - {0}  {1} MB  sig={2}" -f $_.Name, [math]::Round($_.Length/1MB,1), $s.Status)
        }
    }
}

Write-Host ""
if ($fail -eq 0) {
    Write-Host "Staged core: 4/4 intact." -ForegroundColor Green
    exit 0
} else {
    Write-Host ("Staged core: {0} problem(s). Re-download before trusting this stick." -f $fail) -ForegroundColor Red
    exit 1
}
