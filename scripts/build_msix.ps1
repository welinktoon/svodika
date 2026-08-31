[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$version = ([regex]::Match(
    (Get-Content -Raw -Encoding UTF8 (Join-Path $projectRoot "version.py")),
    '__version__\s*=\s*"(?<version>\d+\.\d+\.\d+)"'
)).Groups["version"].Value

if ([string]::IsNullOrWhiteSpace($version)) {
    throw "Could not read the application version."
}

$makeAppx = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter MakeAppx.exe |
    Where-Object { $_.FullName -match '\\x64\\MakeAppx\.exe$' } |
    Sort-Object FullName -Descending |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $makeAppx) {
    throw "MakeAppx.exe was not found. Install the Windows SDK first."
}

$packageRoot = Join-Path $projectRoot "store-package"
$stagingRoot = Join-Path $packageRoot "staging"
$outputPackage = Join-Path $packageRoot "Svodika-$version-x64.msix"
$distributionRoot = Join-Path $projectRoot "dist\MeetingRecorder"
$manifest = Join-Path $projectRoot "packaging\msix\Package.appxmanifest"
$sourceLogo = Join-Path $projectRoot "ui_qt\assets\meeting-recorder-logo-256.png"

if (-not (Test-Path -LiteralPath $distributionRoot)) { throw "Build output is missing: $distributionRoot" }
if (-not (Test-Path -LiteralPath $sourceLogo)) { throw "Source logo is missing: $sourceLogo" }

if (Test-Path -LiteralPath $stagingRoot) { Remove-Item -LiteralPath $stagingRoot -Recurse -Force }
if (Test-Path -LiteralPath $outputPackage) { Remove-Item -LiteralPath $outputPackage -Force }
New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null

robocopy $distributionRoot $stagingRoot /E /COPY:DAT /DCOPY:T /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Could not copy the desktop distribution to the MSIX staging folder." }

$stagedManifest = Join-Path $stagingRoot "AppxManifest.xml"
Copy-Item -LiteralPath $manifest -Destination $stagedManifest
$packageVersion = "$version.0"
$manifestText = Get-Content -Raw -Encoding UTF8 $stagedManifest
$manifestText = $manifestText -replace 'Version="\d+\.\d+\.\d+\.\d+"', "Version=`"$packageVersion`""
Set-Content -LiteralPath $stagedManifest -Value $manifestText -Encoding UTF8
$assetsRoot = Join-Path $stagingRoot "Assets"
New-Item -ItemType Directory -Path $assetsRoot -Force | Out-Null

Add-Type -AssemblyName System.Drawing
function Save-SquareLogo([int]$size, [string]$name) {
    $source = [System.Drawing.Image]::FromFile($sourceLogo)
    try {
        $canvas = New-Object System.Drawing.Bitmap($size, $size)
        $graphics = [System.Drawing.Graphics]::FromImage($canvas)
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $scale = [Math]::Min($size / $source.Width, $size / $source.Height)
            $width = [int]($source.Width * $scale)
            $height = [int]($source.Height * $scale)
            $graphics.DrawImage($source, [int](($size - $width) / 2), [int](($size - $height) / 2), $width, $height)
            $canvas.Save((Join-Path $assetsRoot $name), [System.Drawing.Imaging.ImageFormat]::Png)
        } finally { $graphics.Dispose(); $canvas.Dispose() }
    } finally { $source.Dispose() }
}

Save-SquareLogo 44 "Square44x44Logo.png"
Save-SquareLogo 71 "Square71x71Logo.png"
Save-SquareLogo 150 "Square150x150Logo.png"
Save-SquareLogo 310 "Square310x310Logo.png"
Save-SquareLogo 50 "StoreLogo.png"

$source = [System.Drawing.Image]::FromFile($sourceLogo)
try {
    $wide = New-Object System.Drawing.Bitmap(310, 150)
    $graphics = [System.Drawing.Graphics]::FromImage($wide)
    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $height = 150
        $width = [int]($source.Width * ($height / $source.Height))
        $graphics.DrawImage($source, [int]((310 - $width) / 2), 0, $width, $height)
        $wide.Save((Join-Path $assetsRoot "Wide310x150Logo.png"), [System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $graphics.Dispose(); $wide.Dispose() }
} finally { $source.Dispose() }

& $makeAppx pack /d $stagingRoot /p $outputPackage /o
if ($LASTEXITCODE -ne 0) { throw "MakeAppx could not create the MSIX package." }

Write-Host "MSIX created: $outputPackage"
