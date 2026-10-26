<#
Refresh the vendored PBIR JSON schemas from github.com/microsoft/json-schemas (branch main).

The files mirror the schema URLs: https://developer.microsoft.com/json-schemas/<path> is <path> here.
Without -Schema every schema already vendored is downloaded again; with -Schema only those paths (for
example fabric/item/report/definition/page/2.2.0/schema.json, the URL a newly pulled page.json names).
Schemas they reference through $ref are fetched too. Vendored 2026-10-26 from commit af7b264 (main).
After a refresh, run the local tests: they check that every keyword in the vendored set is one
scripts/hooks/pbir_validate.py implements.

Usage: pwsh templates/pbir-schemas/refresh-schemas.ps1 [-Schema <path> ...]
#>
param([string[]]$Schema)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$base = 'https://raw.githubusercontent.com/microsoft/json-schemas/main/'
if (-not $Schema) {
    $Schema = Get-ChildItem -Path $root -Recurse -Filter *.json |
        ForEach-Object { $_.FullName.Substring($root.Length + 1).Replace('\', '/') }
}
$seen = @{}
$queue = New-Object System.Collections.Generic.Queue[string]
foreach ($s in $Schema) { $queue.Enqueue($s.TrimStart('/')) }
while ($queue.Count -gt 0) {
    $rel = $queue.Dequeue()
    if ($seen.ContainsKey($rel)) { continue }
    $seen[$rel] = $true
    $text = (Invoke-WebRequest -UseBasicParsing -Uri ($base + $rel)).Content
    $null = $text | ConvertFrom-Json  # refuse to vendor anything that is not JSON
    $dest = Join-Path $root $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    [System.IO.File]::WriteAllText($dest, $text, (New-Object System.Text.UTF8Encoding $false))
    $dir = Split-Path (Join-Path $root $rel)
    foreach ($m in [regex]::Matches($text, '"\$ref"\s*:\s*"([^"#]+)')) {
        $full = [System.IO.Path]::GetFullPath((Join-Path $dir $m.Groups[1].Value))
        if (-not $full.StartsWith($root)) { throw "$rel references $($m.Groups[1].Value), outside the schema tree" }
        $queue.Enqueue($full.Substring($root.Length + 1).Replace('\', '/'))
    }
}
"Refreshed $($seen.Count) schema files on $((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')); record the date in docs/PLATFORM-FACTS.md."
