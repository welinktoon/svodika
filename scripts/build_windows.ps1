[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$PythonExecutable = "python",
    [string]$InnoSetupCompiler = "",
    [switch]$SkipDependencyInstall,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ([string]::IsNullOrWhiteSpace($Version)) {
    $versionSource = Get-Content -Raw -Encoding UTF8 (
        Join-Path $projectRoot "version.py"
    )
    $match = [regex]::Match(
        $versionSource,
        '__version__\s*=\s*"(?<version>\d+\.\d+\.\d+)"'
    )
    if (-not $match.Success) {
        throw "Could not read the version from version.py."
    }
    $Version = $match.Groups["version"].Value
}

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must use the X.Y.Z format: $Version"
}

Push-Location $projectRoot
try {
    if (-not $SkipDependencyInstall) {
        & $PythonExecutable -m pip install --upgrade pip
        & $PythonExecutable -m pip install `
            -r requirements.txt `
            -r requirements-gpu.txt `
            -r requirements-build.txt
    }

    if (-not $SkipTests) {
        $env:QT_QPA_PLATFORM = "offscreen"
        & $PythonExecutable -m pytest -q
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed."
        }
    }

    $env:MEETING_RECORDER_VERSION = $Version
    & $PythonExecutable -m PyInstaller `
        --clean `
        --noconfirm `
        (Join-Path $projectRoot "packaging\meeting-recorder.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed."
    }

    $distributionRoot = Join-Path $projectRoot "dist\MeetingRecorder"
    foreach ($requiredCudaDll in @(
        "cublas64_12.dll",
        "cudart64_12.dll",
        "cudnn64_9.dll"
    )) {
        $bundledDll = Get-ChildItem `
            -LiteralPath $distributionRoot `
            -Recurse `
            -File `
            -Filter $requiredCudaDll |
            Select-Object -First 1
        if (-not $bundledDll) {
            throw "Required CUDA runtime DLL was not bundled: $requiredCudaDll"
        }
    }

    $compilerCandidates = @(
        $InnoSetupCompiler,
        $env:INNO_SETUP_COMPILER,
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    $iscc = $compilerCandidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if (-not $iscc) {
        $isccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
        if ($isccCommand) {
            $iscc = $isccCommand.Source
        }
    }
    if (-not $iscc) {
        throw "Inno Setup 6 was not found. Install it with: winget install JRSoftware.InnoSetup"
    }

    & $iscc "/DMyAppVersion=$Version" (
        Join-Path $projectRoot "packaging\installer.iss"
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed."
    }

    $installer = Join-Path (
        Join-Path $projectRoot "installer-output"
    ) "MeetingRecorderSetup-$Version.exe"
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "Installer was not created: $installer"
    }

    $checksum = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksumFile = "$installer.sha256"
    "$checksum  $(Split-Path -Leaf $installer)" |
        Set-Content -LiteralPath $checksumFile -Encoding ASCII

    Write-Host ""
    Write-Host "Build complete:"
    Write-Host "  $installer"
    Write-Host "  $checksumFile"
}
finally {
    Pop-Location
}
