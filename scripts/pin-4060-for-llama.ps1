# pin-4060-for-llama.ps1
# RTX 4060 Ti is for llama-server only. Desktop / DWM / browsers / Orca use the 9900X iGPU.
#
# GpuPreference (HKCU\Software\Microsoft\DirectX\UserGpuPreferences):
#   1 = Power saving (iGPU)
#   2 = High performance (4060 Ti)
#
# DWM/explorer prefs do not take effect until the next logoff.
# Do not disable the NVIDIA device. Do not start local-llm from this script.

$ErrorActionPreference = 'Stop'
$key = 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'
if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }

$highPerf = @(
    'D:\Workspace\Infrastructure\llama-cpp-server-cuda-b10488\llama-server.exe'
)

$powerSaving = @(
    'C:\Windows\System32\dwm.exe',
    'C:\Windows\explorer.exe',
    'C:\Windows\System32\ShellHost.exe',
    'C:\Windows\SystemApps\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy\StartMenuExperienceHost.exe',
    'C:\Windows\SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\SearchHost.exe',
    'C:\Windows\SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\CrossDeviceResume.exe',
    "$env:LOCALAPPDATA\Programs\Orca\orca.exe",
    "$env:LOCALAPPDATA\Programs\orca\Orca.exe",
    'C:\Program Files\Google\Chrome\Application\chrome.exe',
    'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    'C:\Program Files\Mozilla Firefox\firefox.exe',
    "$env:LOCALAPPDATA\Programs\Microsoft VS Code\Code.exe"
)

Set-ItemProperty -Path $key -Name 'DirectXUserGlobalSettings' -Value 'AutoSelectHighPerformanceGPU=0;'

foreach ($exe in $highPerf) {
    if (Test-Path -LiteralPath $exe) {
        Set-ItemProperty -Path $key -Name $exe -Value 'GpuPreference=2;'
        Write-Host "4060: $exe"
    } else {
        Write-Host "skip missing (4060): $exe"
    }
}

foreach ($exe in $powerSaving) {
    if (Test-Path -LiteralPath $exe) {
        Set-ItemProperty -Path $key -Name $exe -Value 'GpuPreference=1;'
        Write-Host "iGPU: $exe"
    } else {
        Write-Host "skip missing (iGPU): $exe"
    }
}

Write-Host ''
Write-Host 'Log off for DWM/explorer/ShellHost to leave the 4060. Do not start local-llm until nvidia-smi compute-apps is llama-only.'
