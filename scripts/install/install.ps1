param(
    [Parameter(Position=0)]
    [string]$Version = "latest"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ReleaseRepo = if ([string]::IsNullOrWhiteSpace($env:CODEX_RELEASE_REPO)) { "mawad-amd/codex" } else { $env:CODEX_RELEASE_REPO }

function Write-Step {
    param(
        [string]$Message
    )

    Write-Host "==> $Message"
}

function Normalize-Version {
    param(
        [string]$RawVersion
    )

    if ([string]::IsNullOrWhiteSpace($RawVersion) -or $RawVersion -eq "latest") {
        return "latest"
    }

    if ($RawVersion.StartsWith("rust-v")) {
        return $RawVersion.Substring(6)
    }

    if ($RawVersion.StartsWith("v")) {
        return $RawVersion.Substring(1)
    }

    return $RawVersion
}

function Get-ReleaseUrl {
    param(
        [string]$AssetName,
        [string]$ResolvedVersion
    )

    return "https://github.com/$ReleaseRepo/releases/download/rust-v$ResolvedVersion/$AssetName"
}

function Path-Contains {
    param(
        [string]$PathValue,
        [string]$Entry
    )

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $false
    }

    $needle = $Entry.TrimEnd("\")
    foreach ($segment in $PathValue.Split(";", [System.StringSplitOptions]::RemoveEmptyEntries)) {
        if ($segment.TrimEnd("\") -ieq $needle) {
            return $true
        }
    }

    return $false
}

function Resolve-Version {
    $normalizedVersion = Normalize-Version -RawVersion $Version
    if ($normalizedVersion -ne "latest") {
        return $normalizedVersion
    }

    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$ReleaseRepo/releases/latest"
    if (-not $release.tag_name) {
        Write-Error "Failed to resolve the latest Codex IntelliKit release version."
        exit 1
    }

    return (Normalize-Version -RawVersion $release.tag_name)
}

if ($env:OS -ne "Windows_NT") {
    Write-Error "install.ps1 supports Windows only. Use install.sh on macOS or Linux."
    exit 1
}

if (-not [Environment]::Is64BitOperatingSystem) {
    Write-Error "Codex requires a 64-bit version of Windows."
    exit 1
}

$architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
$target = $null
$platformLabel = $null
$npmTag = $null
switch ($architecture) {
    "Arm64" {
        $target = "aarch64-pc-windows-msvc"
        $platformLabel = "Windows (ARM64)"
        $npmTag = "win32-arm64"
    }
    "X64" {
        $target = "x86_64-pc-windows-msvc"
        $platformLabel = "Windows (x64)"
        $npmTag = "win32-x64"
    }
    default {
        Write-Error "Unsupported architecture: $architecture"
        exit 1
    }
}

if ([string]::IsNullOrWhiteSpace($env:CODEX_INSTALL_DIR)) {
    $installDir = Join-Path $env:LOCALAPPDATA "Programs\OpenAI\CodexIntelliKit\bin"
} else {
    $installDir = $env:CODEX_INSTALL_DIR
}

$codexPath = Join-Path $installDir "codex-intellikit.exe"
$installMode = if (Test-Path $codexPath) { "Updating" } else { "Installing" }

Write-Step "$installMode Codex IntelliKit CLI"
Write-Step "Detected platform: $platformLabel"

New-Item -ItemType Directory -Force -Path $installDir | Out-Null

$resolvedVersion = Resolve-Version
Write-Step "Resolved version: $resolvedVersion"
$packageAsset = "codex-intellikit-npm-$npmTag-$resolvedVersion.tgz"

$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("codex-install-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

try {
    $archivePath = Join-Path $tempDir $packageAsset
    $extractDir = Join-Path $tempDir "extract"
    $url = Get-ReleaseUrl -AssetName $packageAsset -ResolvedVersion $resolvedVersion

    Write-Step "Downloading Codex IntelliKit CLI"
    Invoke-WebRequest -Uri $url -OutFile $archivePath

    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    tar -xzf $archivePath -C $extractDir

    $vendorRoot = Join-Path $extractDir "package/vendor/$target"
    Write-Step "Installing to $installDir"
    $copyMap = @{
        "codex-intellikit/codex-intellikit.exe" = "codex-intellikit.exe"
        "codex-intellikit/codex-command-runner.exe" = "codex-command-runner.exe"
        "codex-intellikit/codex-windows-sandbox-setup.exe" = "codex-windows-sandbox-setup.exe"
        "path/rg.exe" = "rg.exe"
    }

    foreach ($relativeSource in $copyMap.Keys) {
        $sourcePath = Join-Path $vendorRoot $relativeSource
        $destinationPath = Join-Path $installDir $copyMap[$relativeSource]
        Move-Item -Force $sourcePath $destinationPath
    }
} finally {
    Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$pathNeedsNewShell = $false
if (-not (Path-Contains -PathValue $userPath -Entry $installDir)) {
    if ([string]::IsNullOrWhiteSpace($userPath)) {
        $newUserPath = $installDir
    } else {
        $newUserPath = "$installDir;$userPath"
    }

    [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
    if (-not (Path-Contains -PathValue $env:Path -Entry $installDir)) {
        if ([string]::IsNullOrWhiteSpace($env:Path)) {
            $env:Path = $installDir
        } else {
            $env:Path = "$installDir;$env:Path"
        }
    }
    Write-Step "PATH updated for future PowerShell sessions."
    $pathNeedsNewShell = $true
} elseif (Path-Contains -PathValue $env:Path -Entry $installDir) {
    Write-Step "$installDir is already on PATH."
} else {
    Write-Step "PATH is already configured for future PowerShell sessions."
    $pathNeedsNewShell = $true
}

if ($pathNeedsNewShell) {
    Write-Step ('Run now: $env:Path = "{0};$env:Path"; codex-intellikit' -f $installDir)
    Write-Step "Or open a new PowerShell window and run: codex-intellikit"
} else {
    Write-Step "Run: codex-intellikit"
}

Write-Host "Codex IntelliKit CLI $resolvedVersion installed successfully."
