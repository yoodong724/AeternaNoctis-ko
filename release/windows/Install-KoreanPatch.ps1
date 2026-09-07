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
        # File.Replace is not supported by the WSL UNC provider. Local NTFS uses
        # the atomic path above; this guarded fallback is still covered by the
        # verified backup and whole-install recovery block.
        Move-Item -LiteralPath $StagePath -Destination $Destination -Force
    }
}

if ([string]::IsNullOrWhiteSpace($GameRoot)) {
    $GameRoot = Split-Path -Parent $PSScriptRoot
}
$GameRoot = [System.IO.Path]::GetFullPath($GameRoot)
$manifestPath = Join-Path $PSScriptRoot "manifest.json"
$xdelta = Join-Path $PSScriptRoot "bin\xdelta3.exe"

if (-not (Test-Path -LiteralPath $xdelta -PathType Leaf)) {
    throw "Missing xdelta3: $xdelta"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-Hash $xdelta $manifest.tool.binary_sha256 "xdelta3"

foreach ($item in @($manifest.compatibility)) {
    $path = Resolve-GamePath $GameRoot $item.relative_path
    Assert-Hash $path $item.sha256 $item.relative_path
}

$backupRoot = Join-Path $GameRoot (".aeterna-ko-backup\" + $manifest.patch_version)
if (Test-Path -LiteralPath $backupRoot) {
    throw "Backup already exists. Uninstall the current patch or move the backup first: $backupRoot"
}

$stageRoot = Join-Path $GameRoot (".aeterna-ko-stage-" + [Guid]::NewGuid().ToString("N"))
$backupCreated = $false
try {
    New-Item -ItemType Directory -Path $stageRoot | Out-Null
    $staged = @()
    foreach ($item in @($manifest.files)) {
        $sourcePath = Resolve-GamePath $GameRoot $item.relative_path
        $patchPath = Resolve-GamePath $PSScriptRoot $item.patch_path
        Assert-Hash $sourcePath $item.source_sha256 $item.relative_path
        Assert-Hash $patchPath $item.patch_sha256 $item.patch_path
        $stagePath = Join-Path $stageRoot ([Guid]::NewGuid().ToString("N") + ".tmp")
        & $xdelta -d -f -s $sourcePath $patchPath $stagePath
        if ($LASTEXITCODE -ne 0) {
            throw "xdelta3 failed for $($item.relative_path) with exit code $LASTEXITCODE"
        }
        Assert-Hash $stagePath $item.target_sha256 ("decoded " + $item.relative_path)
        $staged += [PSCustomObject]@{ Item = $item; StagePath = $stagePath; Destination = $sourcePath }
    }

    New-Item -ItemType Directory -Path $backupRoot | Out-Null
    $backupCreated = $true
    foreach ($row in $staged) {
        $backupPath = Resolve-GamePath $backupRoot $row.Item.relative_path
        New-Item -ItemType Directory -Path (Split-Path -Parent $backupPath) -Force | Out-Null
        Copy-Item -LiteralPath $row.Destination -Destination $backupPath
        Assert-Hash $backupPath $row.Item.source_sha256 ("backup " + $row.Item.relative_path)
    }

    foreach ($row in $staged) {
        Replace-VerifiedFile $row.StagePath $row.Destination
        Assert-Hash $row.Destination $row.Item.target_sha256 ("installed " + $row.Item.relative_path)
    }
    Write-Host "Aeterna Noctis Korean Patch $($manifest.patch_version) installed successfully."
    Write-Host "Select Japanese in the game language menu to use Korean."
    Write-Host "Backup: $backupRoot"
}
catch {
    if ($backupCreated) {
        Write-Warning "Installation failed. Restoring verified originals from backup."
        $restoreSucceeded = $true
        foreach ($item in @($manifest.files)) {
            $backupPath = Resolve-GamePath $backupRoot $item.relative_path
            $destination = Resolve-GamePath $GameRoot $item.relative_path
            if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
                try {
                    Copy-Item -LiteralPath $backupPath -Destination $destination -Force
                    Assert-Hash $destination $item.source_sha256 ("recovered " + $item.relative_path)
                }
                catch {
                    $restoreSucceeded = $false
                    Write-Warning "Automatic recovery failed for $($item.relative_path). Keep backup: $backupRoot"
                }
            }
        }
        if ($restoreSucceeded -and (Test-Path -LiteralPath $backupRoot)) {
            Remove-Item -LiteralPath $backupRoot -Recurse -Force
        }
    }
    throw
}
finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}
