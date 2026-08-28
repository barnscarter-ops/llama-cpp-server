$samples = (Get-Counter '\GPU Process Memory(*)\Dedicated Usage','\GPU Process Memory(*)\Shared Usage' -ErrorAction SilentlyContinue).CounterSamples |
    Where-Object { $_.CookedValue -gt 100MB } |
    Sort-Object CookedValue -Descending
foreach ($s in $samples) {
    $proc = ($s.InstanceName -split '\\')[-1]
    '{0,-10} {1,8:N0} MB  pid={2}' -f $s.SetType, ($s.CookedValue/1MB), $proc
}
