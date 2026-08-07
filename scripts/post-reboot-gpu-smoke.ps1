# post-reboot GPU stack smoke — run elevated after full-stack fix reboot
$ErrorActionPreference = "Continue"
Write-Host "=== Boot time ===" (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Write-Host "=== TDR keys (expect 60/60/120/10) ==="
reg query "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v TdrDelay
reg query "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v TdrDdiDelay
Write-Host "=== MemoryCompression (expect False) ==="
Get-MMAgent | Select-Object MemoryCompression
Write-Host "=== Display devices (no NVIDIA) ==="
pnputil /enum-devices /class Display
Write-Host "=== Vulkan ICDs (AMD only) ==="
reg query "HKLM\SOFTWARE\Khronos\Vulkan\Drivers"
Write-Host "=== Newest WATCHDOG ==="
Get-ChildItem C:\Windows\LiveKernelReports\WATCHDOG -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3 Name, LastWriteTime
Write-Host "=== GPU ==="
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, Status | Format-Table -AutoSize
Write-Host "DONE — if clean, start ONLY qwen3-llama-vulkan via elevated pm2, one short gen, then controlled stop/start with 60s gap"
