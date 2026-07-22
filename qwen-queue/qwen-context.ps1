# qwen-context.ps1 — pick -ContextFiles for qwen-submit.ps1 via code-review-graph.
#
# Usage:
#   $ctx = & .\qwen-context.ps1 -Query "stale","scheduled-tasks" -Repo C:\Workspace\Shared\Agents\Hermes-Supervisor
#   & .\qwen-submit.ps1 -Task "..." -ContextFiles $ctx -Wait
#
# The graph must exist for the repo first:  code-review-graph build --repo <repo>
# Refresh after big changes:                code-review-graph update --repo <repo>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string[]]$Query,

    [string]$Repo = (Get-Location).Path,

    [ValidateRange(1, 50)]
    [int]$LimitPerTerm = 5
)

$ErrorActionPreference = "Stop"
$exe = Join-Path $PSScriptRoot "code-review-graph\.venv\Scripts\code-review-graph.exe"
if (-not (Test-Path $exe)) { throw "code-review-graph not installed at $exe" }

# ponytail: one FTS search per keyword, union the file paths. Ceiling is FTS
# keyword matching; upgrade path is `code-review-graph embed` + semantic search
# or the MCP server (`code-review-graph serve`).
$files = foreach ($term in $Query) {
    $json = & $exe search $term --repo $Repo --limit $LimitPerTerm 2>$null | ConvertFrom-Json
    if ($json.status -eq "ok") { $json.results | ForEach-Object { $_.file_path } }
}

$files | Where-Object { $_ } | Sort-Object -Unique
