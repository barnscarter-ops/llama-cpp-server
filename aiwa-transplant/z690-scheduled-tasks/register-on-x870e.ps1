# Re-register the Z690's exported scheduled tasks on the X870E.
# Skips hardware/vendor tasks that don't apply to this machine, swaps the old
# account SID for the current user, and reports per-task results.
$dir  = $PSScriptRoot
$skip = 'AMD Install Manager - Check For Updates','AcPowerNotification','ArmourySocketServer',
        'AsusDriverHub','NoiseCancelingEngine','P508PowerAgent_sdk','HWiNFO','DeleteExecutorTest',
        'Grizzly FB Page Token Renewal',  # already registered 2026-08-18
        'SANDISK','Start SANDISK Drive Service','Restart SANDISK Drive Service on bootup or resume',
        'StartDVR','StartCN','StartCNBM'
$me  = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $me.User.Value
$ok = @(); $fail = @()
Get-ChildItem $dir -Filter '*.xml' | ForEach-Object {
  $name = $_.BaseName
  if ($skip -contains $name) { Write-Host "skip     $name"; return }
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Write-Host "exists   $name"; $ok += $name; return }
  $xml = Get-Content $_.FullName -Raw
  $xml = $xml -replace 'S-1-5-21-3692995547-1880394738-1435933407-1001', $sid
  $xml = $xml -replace '<UserId>CARTERSPC\\carte</UserId>', "<UserId>$($me.Name)</UserId>"
  try {
    Register-ScheduledTask -Xml $xml -TaskName $name -ErrorAction Stop | Out-Null
    Write-Host "OK       $name"; $ok += $name
  } catch {
    Write-Host "FAIL     $name :: $($_.Exception.Message.Split("`n")[0])"
    $fail += $name
  }
}
Write-Host ""
Write-Host "Registered/present: $($ok.Count)   Failed: $($fail.Count)"
if ($fail) { $fail | ForEach-Object { Write-Host "  FAILED: $_" } }
