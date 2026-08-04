$ErrorActionPreference = 'Stop'

$repoRoot   = Split-Path -Parent $PSScriptRoot
$scriptsDir = Join-Path $repoRoot 'scripts'

function Format-PathEntry {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    return ($Path.TrimEnd('\').ToLower())
}

Write-Host ''
Write-Host 'OpenWhisper uninstaller'
Write-Host '-----------------------'
Write-Host ''

$currentPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$entries = @()
if ($currentPath) { $entries = $currentPath -split ';' | Where-Object { $_ } }

$normalizedTarget = Format-PathEntry $scriptsDir
$kept = @()
$removedCount = 0
foreach ($entry in $entries) {
    if ((Format-PathEntry $entry) -eq $normalizedTarget) {
        $removedCount++
    } else {
        $kept += $entry
    }
}

if ($removedCount -eq 0) {
    Write-Host "[ok] $scriptsDir is not on your user PATH. Nothing to do."
} else {
    [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
    Write-Host "[ok] Removed $scriptsDir from your user PATH ($removedCount entry/entries)"
}

Write-Host ''
Write-Host 'Open a new terminal for the change to take effect.'
Write-Host ''
Write-Host 'Note: this only edits your PATH. The venv, source code, and'
Write-Host '      scripts/openwhisper.cmd are left untouched.'
Write-Host ''
