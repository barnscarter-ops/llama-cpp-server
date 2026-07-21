[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateLength(1, 12000)]
    [string]$Task,

    [string]$Source = "manual",
    [ValidateRange(0, 100000)]
    [int]$ExpectedLines = 25,
    [ValidateRange(1, 1000)]
    [int]$ExpectedFiles = 1,
    [ValidateSet("low", "medium", "high")]
    [string]$Risk = "low",
    [string]$BaseUrl = "http://127.0.0.1:8080",
    [switch]$Wait,
    [ValidateRange(10, 3600)]
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$request = @{
    model = "qwen3.6-35b"
    stream = $false
    temperature = 0.1
    messages = @(
        @{
            role = "system"
            content = "You are a bounded coding executor. Do not claim to change files. Return a concise unified diff and the exact verification commands for the calling harness to review and apply."
        },
        @{ role = "user"; content = $Task }
    )
}
$body = @{
    source = $Source
    idempotency_key = [guid]::NewGuid().ToString("N")
    decision_context = @{
        summary = $Task
        expected_lines = $ExpectedLines
        expected_files = $ExpectedFiles
        risk = $Risk
    }
    request = $request
} | ConvertTo-Json -Depth 8

$submitted = Invoke-RestMethod -Method Post -Uri "$BaseUrl/__guardian/jobs" -ContentType "application/json" -Body $body
if (-not $Wait -or -not $submitted.job_id) {
    $submitted | ConvertTo-Json -Depth 12
    return
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    Start-Sleep -Seconds 2
    $status = Invoke-RestMethod -Method Get -Uri "$BaseUrl/__guardian/jobs/$($submitted.job_id)"
} while ($status.status -in @("queued", "running") -and (Get-Date) -lt $deadline)

$status | ConvertTo-Json -Depth 12
if ($status.status -in @("queued", "running")) {
    throw "Timed out waiting for Qwen job $($submitted.job_id)."
}
