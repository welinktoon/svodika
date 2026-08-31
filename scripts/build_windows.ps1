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

    # Qt uses the Windows ICU compatibility shims. A third-party ICU runtime
    # earlier on PATH (for example, Poppler's) can be mistaken for those shims
    # by PyInstaller and then shadow the Windows DLLs in the frozen app.
    $originalBuildPath = $env:PATH
    $windowsSystemPath = [IO.Path]::GetFullPath(
        (Join-Path $env:WINDIR "System32")
    ).TrimEnd("\")
    $cleanBuildPath = foreach ($pathEntry in ($env:PATH -split ";")) {
        if ([string]::IsNullOrWhiteSpace($pathEntry)) {
            continue
        }

        $unquotedPathEntry = $pathEntry.Trim().Trim('"')
        try {
            $resolvedPathEntry = [IO.Path]::GetFullPath(
                $unquotedPathEntry
            ).TrimEnd("\")
        }
        catch {
            $pathEntry
            continue
        }

        $icuShim = Join-Path $resolvedPathEntry "icuuc.dll"
        $isWindowsSystemPath = $resolvedPathEntry.Equals(
            $windowsSystemPath,
            [StringComparison]::OrdinalIgnoreCase
        )
        if (
            -not $isWindowsSystemPath -and
            (Test-Path -LiteralPath $icuShim -PathType Leaf)
        ) {
            Write-Host "Ignoring build PATH entry with a foreign ICU runtime: $resolvedPathEntry"
            continue
        }

        $pathEntry
    }

    $pyInstallerExitCode = 1
    try {
        $env:PATH = $cleanBuildPath -join ";"
        & $PythonExecutable -m PyInstaller `
            --clean `
            --noconfirm `
            (Join-Path $projectRoot "packaging\meeting-recorder.spec")
        $pyInstallerExitCode = $LASTEXITCODE
    }
    finally {
        $env:PATH = $originalBuildPath
    }
    if ($pyInstallerExitCode -ne 0) {
        throw "PyInstaller failed."
    }

    $distributionRoot = Join-Path $projectRoot "dist\MeetingRecorder"
    $bundledIcuShims = Get-ChildItem `
        -LiteralPath (Join-Path $distributionRoot "_internal") `
        -File `
        -Filter "icu*.dll"
    if ($bundledIcuShims) {
        $bundledIcuNames = $bundledIcuShims.Name -join ", "
        throw "Unexpected ICU DLLs would shadow the Windows runtime: $bundledIcuNames"
    }

    $sensitiveRuntimeNames = @(
        ".env",
        "auth.json",
        "credentials.json",
        "openwhisper_settings.json",
        "transcription_history.json"
    )
    $bundledSensitiveFiles = Get-ChildItem `
        -LiteralPath $distributionRoot `
        -Recurse `
        -File |
        Where-Object {
            $sensitiveRuntimeNames -contains $_.Name.ToLowerInvariant()
        }
    if ($bundledSensitiveFiles) {
        $bundledNames = $bundledSensitiveFiles.FullName -join ", "
        throw "Sensitive user files were bundled: $bundledNames"
    }

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
