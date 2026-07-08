# Run the Slack bridge in the background (logs to slack_bridge.log)
# Usage: .\start-slack-bridge.ps1
#        .\start-slack-bridge.ps1 -foreground   (to see live output)

param([switch]$Foreground)

$script = Join-Path $PSScriptRoot "slack_agent_bridge.py"
$log    = Join-Path $PSScriptRoot "slack_bridge.log"

if ($Foreground) {
    python $script
} else {
    Start-Process -FilePath "python" `
                  -ArgumentList $script `
                  -RedirectStandardOutput $log `
                  -RedirectStandardError  $log `
                  -WindowStyle Hidden `
                  -PassThru | ForEach-Object {
        Write-Host "Slack bridge started (PID $($_.Id)). Log: $log" -ForegroundColor Green
        Write-Host "To stop: Stop-Process -Id $($_.Id)" -ForegroundColor DarkGray
    }
}
