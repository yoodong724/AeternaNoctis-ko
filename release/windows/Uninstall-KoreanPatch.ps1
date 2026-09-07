param(
    [string]$GameRoot = ""
)

$ErrorActionPreference = "Stop"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Resolve-GamePath([string]$Root, [string]$RelativePath) {
    $native = $RelativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    return [System.IO.Path]::GetFullPath((Join-Path $Root $native))
}

function Assert-Hash([string]$Path, [string]$Expected, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing $Label`: $Path"
    }
    $actual = Get-Sha256 $Path
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "Hash mismatch for $Label. Expected $Expected, got $actual"
    }
}

function Replace-VerifiedFile([string]$StagePath, [string]$Destination) {
    try {
        [System.IO.File]::Replace($StagePath, $Destination, $null)
    }
    catch [System.ArgumentException] {
        Move-Item -LiteralPath $StagePath -Destination $Destination -Force
    }
}

if ([string]::IsNullOrWhiteSpace($GameRoot)) {
    $GameRoot = Split-Path -Parent $PSScriptRoot
}
$GameRoot = [System.IO.Path]::GetFullPath($GameRoot)
$manifestPath = Join-Path $PSScriptRoot "manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$backupRoot = Join-Path $GameRoot (".aeterna-ko-backup\" + $manifest.patch_version)
if (-not (Test-Path -LiteralPath $backupRoot -PathType Container)) {
    throw "Verified backup is missing: $backupRoot"
}

foreach ($item in @($manifest.files)) {
    $destination = Resolve-GamePath $GameRoot $item.relative_path
    $backupPath = Resolve-GamePath $backupRoot $item.relative_path
    Assert-Hash $destination $item.target_sha256 ("installed " + $item.relative_path)
    Assert-Hash $backupPath $item.source_sha256 ("backup " + $item.relative_path)
}

$stageRoot = Join-Path $GameRoot (".aeterna-ko-rollback-stage-" + [Guid]::NewGuid().ToString("N"))
try {
    New-Item -ItemType Directory -Path $stageRoot | Out-Null
    $staged = @()
    foreach ($item in @($manifest.files)) {
        $destination = Resolve-GamePath $GameRoot $item.relative_path
        $backupPath = Resolve-GamePath $backupRoot $item.relative_path
        $originalStage = Join-Path $stageRoot ([Guid]::NewGuid().ToString("N") + ".original")
        $localizedRecovery = Join-Path $stageRoot ([Guid]::NewGuid().ToString("N") + ".localized")
        Copy-Item -LiteralPath $backupPath -Destination $originalStage
        Copy-Item -LiteralPath $destination -Destination $localizedRecovery
        Assert-Hash $originalStage $item.source_sha256 ("staged original " + $item.relative_path)
        Assert-Hash $localizedRecovery $item.target_sha256 ("staged recovery " + $item.relative_path)
        $staged += [PSCustomObject]@{
            Item = $item
            OriginalStage = $originalStage
            LocalizedRecovery = $localizedRecovery
            Destination = $destination
        }
    }

    try {
        foreach ($row in $staged) {
            Replace-VerifiedFile $row.OriginalStage $row.Destination
            Assert-Hash $row.Destination $row.Item.source_sha256 ("restored " + $row.Item.relative_path)
        }
    }
    catch {
        Write-Warning "Rollback failed. Restoring localized files to avoid a mixed state."
        foreach ($row in $staged) {
            if (Test-Path -LiteralPath $row.LocalizedRecovery -PathType Leaf) {
                Copy-Item -LiteralPath $row.LocalizedRecovery -Destination $row.Destination -Force
            }
        }
        throw
    }

    Remove-Item -LiteralPath $backupRoot -Recurse -Force
    $backupParent = Split-Path -Parent $backupRoot
    if ((Test-Path -LiteralPath $backupParent -PathType Container) -and
        -not (Get-ChildItem -LiteralPath $backupParent -Force | Select-Object -First 1)) {
        Remove-Item -LiteralPath $backupParent -Force
    }
    Write-Host "Aeterna Noctis Korean Patch $($manifest.patch_version) removed successfully."
}
finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}
