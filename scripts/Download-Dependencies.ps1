param([string]$Destination = (Join-Path $PSScriptRoot '..\vendor'))
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Destination = [IO.Path]::GetFullPath($Destination)
$lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\vendor-lock.json') -Raw | ConvertFrom-Json
$toolsDir = Join-Path $Destination 'tools'
$licensesDir = Join-Path $Destination 'licenses'
New-Item -ItemType Directory -Force -Path $toolsDir, $licensesDir | Out-Null
$tempDir = Join-Path $Destination ('fetch-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempDir | Out-Null
function Fetch-Verified($Item, [string]$Path) {
    if ($Item.url -notmatch '^https://github\.com/(yt-dlp/yt-dlp|FFmpeg-Builds/FFmpeg-Builds)/releases/download/') { throw 'Unexpected dependency origin' }
    if ($Item.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid dependency hash' }
    Invoke-WebRequest -Uri $Item.url -OutFile $Path -UseBasicParsing
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne $Item.sha256) { throw "SHA256 mismatch for $($Item.version). The dependency lock must be reviewed; no fallback download is allowed." }
}
try {
    Fetch-Verified $lock.yt_dlp (Join-Path $toolsDir 'yt-dlp.exe')
    $archivePath = Join-Path $tempDir 'ffmpeg.zip'
    Fetch-Verified $lock.ffmpeg $archivePath
    $unpacked = Join-Path $tempDir 'ffmpeg'
    Expand-Archive -LiteralPath $archivePath -DestinationPath $unpacked
    $ffmpeg = @(Get-ChildItem -LiteralPath $unpacked -Recurse -File -Filter 'ffmpeg.exe')
    if ($ffmpeg.Count -ne 1) { throw 'FFmpeg archive layout changed' }
    $binDir = $ffmpeg[0].Directory.FullName
    Get-ChildItem -LiteralPath $binDir -File | Where-Object { $_.Name -in @('ffmpeg.exe','ffprobe.exe') -or $_.Extension -eq '.dll' } | Copy-Item -Destination $toolsDir
    $packageRoot = Split-Path $binDir -Parent
    $ffLicenseDir = Join-Path $licensesDir 'ffmpeg'
    New-Item -ItemType Directory -Force -Path $ffLicenseDir | Out-Null
    Get-ChildItem -LiteralPath $packageRoot | Where-Object { $_.Name -ne 'bin' } | Copy-Item -Destination $ffLicenseDir -Recurse -Force
    foreach ($name in @('LICENSE','THIRD_PARTY_LICENSES.txt')) {
        $uri = 'https://raw.githubusercontent.com/yt-dlp/yt-dlp/' + $lock.yt_dlp.version + '/' + $name
        Invoke-WebRequest -Uri $uri -OutFile (Join-Path $licensesDir ('yt-dlp-' + $name)) -UseBasicParsing
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\vendor-lock.json') -Destination $licensesDir
    foreach ($tool in @('yt-dlp.exe','ffmpeg.exe','ffprobe.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $toolsDir $tool) -PathType Leaf)) { throw "Missing $tool" }
    }
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
