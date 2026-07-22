[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateLength(1, 16000)]
    [string]$Task,

    [string]$Source = "manual",
    [ValidateRange(0, 100000)]
    [int]$ExpectedLines = 25,
    [ValidateRange(1, 1000)]
    [int]$ExpectedFiles = 1,
    [ValidateSet("low", "medium", "high")]
    [string]$Risk = "low",

    # File paths to include as repository context. The script reads each file,
    # prepends line numbers, and embeds it in the prompt so Qwen sees real code.
    [string[]]$ContextFiles = @(),

    # Safety cap on total context chars so a stray huge file can't overflow.
    [ValidateRange(1000, 200000)]
    [int]$ContextMaxChars = 80000,

    [string]$BaseUrl = "http://127.0.0.1:8080",
    [switch]$Wait,
    [ValidateRange(10, 3600)]
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"

# ── Build the user message: task description + repo context ──────────────

$userContent = $Task

if ($ContextFiles.Count -gt 0) {
    $contextSb = [System.Text.StringBuilder]::new()
    [void]$contextSb.AppendLine()
    [void]$contextSb.AppendLine()
    [void]$contextSb.AppendLine("--- REPOSITORY CONTEXT (real files, line-numbered) ---")
    $totalChars = 0
    $filesIncluded = 0

    foreach ($file in $ContextFiles) {
        if (-not (Test-Path $file -PathType Leaf)) {
            Write-Warning "Context file not found, skipping: $file"
            continue
        }
        $resolved = (Resolve-Path $file).Path
        $rawLines = Get-Content -Path $file -Encoding UTF8
        if ($null -eq $rawLines) { $rawLines = @() }

        # Per-file estimate so we can bail cleanly on the cap.
        $fileText = ($rawLines -join "`n")
        if ($totalChars + $fileText.Length -gt $ContextMaxChars) {
            Write-Warning "Context char cap ($ContextMaxChars) reached before: $resolved"
            break
        }
        $totalChars += $fileText.Length

        # Triple-backtick fence built from char codes to avoid PowerShell's
        # backtick escape semantics inside double-quoted strings.
        $fence = [string][char]96 + [string][char]96 + [string][char]96
        [void]$contextSb.AppendLine()
        [void]$contextSb.AppendLine("### File: $resolved")
        [void]$contextSb.AppendLine($fence)
        $i = 1
        foreach ($line in $rawLines) {
            [void]$contextSb.AppendLine(('{0,6}|{1}' -f $i, $line))
            $i++
        }
        [void]$contextSb.AppendLine($fence)
        $filesIncluded++
    }

    if ($filesIncluded -gt 0) {
        $userContent += $contextSb.ToString()
        Write-Host "Context: $filesIncluded file(s), ~$totalChars chars injected" -ForegroundColor DarkGray
    }
}

# ── Assemble the OpenAI-compatible request ───────────────────────────────

$request = @{
    model = "qwen3.6-35b"
    stream = $false
    temperature = 0.1
    messages = @(
        @{
            role = "system"
            content = @(
                "You are a bounded coding executor.",
                "You will receive a task description followed by real repository context",
                "(actual file contents with line numbers in LINE|CONTENT format).",
                "Base your changes STRICTLY on the provided file contents.",
                "Do not invent file structure, function signatures, imports, or conventions",
                "that you cannot see in the context.",
                "Return a concise unified diff against the provided files plus the exact",
                "verification commands for the calling harness to review and apply."
            ) -join " "
        },
        @{ role = "user"; content = $userContent }
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
