$ErrorActionPreference = 'Stop'

$repoRoot   = Split-Path -Parent $PSScriptRoot
$scriptsDir = Join-Path $repoRoot 'scripts'
$venvPython = Join-Path $repoRoot 'venv\Scripts\pythonw.exe'

function Format-PathEntry {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    return ($Path.TrimEnd('\').ToLower())
}

Write-Host ''
Write-Host 'OpenWhisper installer'
Write-Host '---------------------'
Write-Host ''

if (-not (Test-Path $venvPython)) {
    Write-Host '[error] Virtual environment not found.' -ForegroundColor Red
    Write-Host "        Expected: $venvPython"
    Write-Host ''
    Write-Host 'Create the venv first, then re-run install.cmd:'
    Write-Host '    python -m venv venv'
    Write-Host '    venv\Scripts\activate'
    Write-Host '    pip install -r requirements.txt'
    Write-Host ''
    exit 1
}
Write-Host "[ok] venv found at $venvPython"

$currentPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$entries = @()
if ($currentPath) { $entries = $currentPath -split ';' | Where-Object { $_ } }

$normalizedTarget = Format-PathEntry $scriptsDir
$alreadyOnPath    = $false
foreach ($entry in $entries) {
    if ((Format-PathEntry $entry) -eq $normalizedTarget) {
        $alreadyOnPath = $true
        break
    }
}

if ($alreadyOnPath) {
    Write-Host "[ok] $scriptsDir is already on your user PATH"
} else {
    $newPath = if ($currentPath) { "$currentPath;$scriptsDir" } else { $scriptsDir }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Host "[ok] Added $scriptsDir to your user PATH"
}

Write-Host ''
Write-Host 'Done. Open a new terminal, then try:'
Write-Host '    ow              (short alias)'
Write-Host '    openwhisper     (full name)'
Write-Host ''
Write-Host 'To undo: run uninstall.cmd'
Write-Host ''
