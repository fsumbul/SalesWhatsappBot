param(
    [string]$Root = "C:\sites\ashiraai\app",
    [string]$BackupRoot = "C:\sites\ashiraai\backups"
)

$ErrorActionPreference = "Stop"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$destination = Join-Path $BackupRoot ("appledouble-all-" + $stamp)
New-Item -ItemType Directory -Path $destination -Force | Out-Null

foreach ($cacheName in @(".mypy_cache", ".ruff_cache")) {
    $cachePath = Join-Path $Root $cacheName
    if (Test-Path $cachePath) {
        Move-Item $cachePath (Join-Path $destination $cacheName) -Force
    }
}

$files = @(Get-ChildItem $Root -Recurse -Force -File | Where-Object Name -Like "._*")
$moved = 0
$skipped = 0
foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath $file.FullName)) {
        $skipped += 1
        continue
    }
    $relative = $file.FullName.Substring($Root.Length).TrimStart("\")
    $target = Join-Path $destination $relative
    New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
    try {
        Move-Item -LiteralPath $file.FullName -Destination $target -Force
        $moved += 1
    } catch {
        $skipped += 1
    }
}

Write-Output ("archive: " + $destination)
Write-Output ("metadata files moved: " + $moved)
Write-Output ("inaccessible metadata entries skipped: " + $skipped)
