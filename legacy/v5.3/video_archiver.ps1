param(
    [Parameter(Mandatory = $false)]
    [string]$VideoUrl
)

# Local Video Archiver v5 - PowerShell 5.1 compatible.
# Downloads publicly accessible or otherwise authorized media URLs only.
# It does not enumerate hidden episodes, sign URLs, authenticate, or bypass DRM.
$ErrorActionPreference = 'Stop'
try { $Host.UI.RawUI.WindowTitle = 'Local Video Archiver v5' } catch {}

# Keep local diagnostic logs without relying on the CMD window staying open.
$script:TranscriptStarted = $false
$script:LogPath = ''
trap {
    Write-Host ''
    Write-Host ('UNEXPECTED ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host ('Line: ' + $_.InvocationInfo.ScriptLineNumber) -ForegroundColor Yellow
    }
    if ($script:TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
        $script:TranscriptStarted = $false
    }
    if ($script:LogPath) { Write-Host ('Log: ' + $script:LogPath) -ForegroundColor Cyan }
    exit 90
}
try {
    $logDir = Join-Path $PSScriptRoot 'Logs'
    if (-not (Test-Path -LiteralPath $logDir -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $logDir -Force)
    }
    $script:LogPath = Join-Path $logDir ('session_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '.txt')
    Start-Transcript -Path $script:LogPath -ErrorAction Stop | Out-Null
    $script:TranscriptStarted = $true
} catch {
    Write-Host ('Warning: could not create diagnostic log: ' + $_.Exception.Message) -ForegroundColor Yellow
}

function Finish([int]$ExitCode = 0) {
    Write-Host ''
    Write-Host ('Result code: ' + $ExitCode)
    if ($script:TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
        $script:TranscriptStarted = $false
    }
    if ($script:LogPath) { Write-Host ('Log: ' + $script:LogPath) -ForegroundColor Cyan }
    exit $ExitCode
}

function Test-HttpUrl([string]$Value) {
    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri)) { return $false }
    return ($uri.Scheme -eq 'https' -or $uri.Scheme -eq 'http')
}

function Get-SafeFolderName([string]$Name) {
    $clean = [regex]::Replace($Name, '[<>:"/\\|?*\x00-\x1F]', '_').Trim([char[]]@(' ', '.'))
    if ([string]::IsNullOrWhiteSpace($clean)) { $clean = 'My_Series' }
    if ($clean -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') { $clean = '_' + $clean }
    if ($clean.Length -gt 75) { $clean = $clean.Substring(0,75) }
    return $clean
}

function Confirm-Permission {
    Write-Host ''
    Write-Host 'Only download videos you are permitted to save.' -ForegroundColor Yellow
    $reply = Read-Host 'Type YES if authorized'
    return ($reply -ceq 'YES')
}

function Ensure-Directory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $Path -Force)
    }
}

function Choose-SeriesFolder([string]$Root) {
    $name = Read-Host 'Series folder name (Enter = My_Series)'
    $safe = Get-SafeFolderName $name
    $location = Join-Path $Root $safe
    Ensure-Directory $location
    return $location
}

Write-Host '-------------------------------------------------' -ForegroundColor Cyan
Write-Host '  LOCAL VIDEO ARCHIVER v5 | Series / Episodes' -ForegroundColor Cyan
Write-Host '-------------------------------------------------' -ForegroundColor Cyan
Write-Host 'For accessible, authorized content only.' -ForegroundColor Gray
Write-Host 'No DRM, paywall, login or access-control bypass.' -ForegroundColor Gray
Write-Host ''

Write-Host 'Choose an action:'
Write-Host '  0 = Exit'
Write-Host '  1 = Check one video or supported playlist (no download)'
Write-Host '  2 = Download ONE accessible video'
Write-Host '  3 = Download ALL videos from a SUPPORTED playlist page'
Write-Host '  4 = Download a SERIES from episodes.txt (one URL per line)'
Write-Host '  5 = Inspect SAVED ReelShort HTML (offline metadata only)'
Write-Host '  6 = Import Series Scanner CSV / authorized media URLs and download authorized media URLs'
$firstInput = Read-Host 'Enter an option (1-6) OR paste a video/series URL'
$firstInput = ([string]$firstInput).Trim().Trim('"')
if ($firstInput -eq '0') { Finish 77 }
if (Test-HttpUrl $firstInput) {
    $VideoUrl = $firstInput
    Write-Host ''
    Write-Host 'URL detected. Now choose what to do with it:' -ForegroundColor Cyan
    Write-Host '  1 = Inspect this link'
    Write-Host '  2 = Download one accessible video'
    Write-Host '  3 = Download a playlist, if supported by yt-dlp'
    $action = Read-Host 'Choose 1, 2 or 3 (Enter = 1)'
    if ([string]::IsNullOrWhiteSpace($action)) { $action = '1' }
    if ($action -notin @('1','2','3')) {
        Write-Host 'Invalid action for a pasted URL. Choose 1, 2 or 3.' -ForegroundColor Yellow
        Finish 1
    }
} else {
    $action = $firstInput
    if ($action -notin @('1','2','3','4','5','6')) {
        Write-Host 'Invalid selection. Choose 0-6 or paste an http(s) URL.' -ForegroundColor Yellow
        Finish 1
    }
}

if ($action -eq '5') {
    & (Join-Path $PSScriptRoot 'inspect_html.ps1')
    Finish $LASTEXITCODE
}
if ($action -eq '6') {
    & (Join-Path $PSScriptRoot 'import_collector.ps1')
    Finish $LASTEXITCODE
}

$localExe = Join-Path $PSScriptRoot 'yt-dlp.exe'
if (Test-Path -LiteralPath $localExe -PathType Leaf) {
    $ytDlp = $localExe
} else {
    $cmd = Get-Command yt-dlp -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        Write-Host 'yt-dlp was not found.' -ForegroundColor Yellow
        Write-Host 'Install with: winget install --source winget --id yt-dlp.yt-dlp'
        Write-Host 'Or place official yt-dlp.exe beside this script.'
        Finish 1
    }
    $ytDlp = $cmd.Source
}


$downloadRoot = Join-Path $PSScriptRoot 'Downloads'
Ensure-Directory $downloadRoot
$archiveFile = Join-Path $PSScriptRoot 'downloaded_ids.txt'

if ($action -eq '4') {
    $defaultList = Join-Path $PSScriptRoot 'episodes.txt'
    if (-not (Test-Path -LiteralPath $defaultList -PathType Leaf)) {
        @(
            '# Put one authorized episode URL per line, in episode order.',
            '# Lines starting with # are ignored.',
            '# Example: https://example.org/episode-01.mp4'
        ) | Set-Content -LiteralPath $defaultList -Encoding UTF8
    }
    Write-Host ''
    Write-Host "Default list file: $defaultList" -ForegroundColor Cyan
    $listPath = Read-Host 'Path to URL list (Enter = episodes.txt)'
    if ([string]::IsNullOrWhiteSpace($listPath)) { $listPath = $defaultList }
    $listPath = $listPath.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $listPath -PathType Leaf)) {
        Write-Host 'List not found. Open episodes.txt, paste URLs, save and retry.' -ForegroundColor Red
        Finish 1
    }

    $urls = New-Object 'System.Collections.Generic.List[string]'
    $seen = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($raw in (Get-Content -LiteralPath $listPath -Encoding UTF8)) {
        $line = $raw.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) { continue }
        if (-not (Test-HttpUrl $line)) {
            Write-Host 'Skipped invalid non-http(s) line.' -ForegroundColor Yellow
            continue
        }
        if ($seen.Add($line)) {
            [void]$urls.Add($line)
        }
    }
    if ($urls.Count -eq 0) {
        Write-Host 'No valid video URLs found in the list.' -ForegroundColor Yellow
        Write-Host 'Edit episodes.txt with one URL per line, then retry.'
        Finish 1
    }

    Write-Host "Found $($urls.Count) distinct URL(s). One URL may still mean one episode." -ForegroundColor Cyan
    if (-not (Confirm-Permission)) { Write-Host 'Cancelled.'; Finish 0 }
    $seriesDir = Choose-SeriesFolder $downloadRoot
    $archiveFile = Join-Path $seriesDir 'downloaded_ids.txt'
    $ok = 0
    $failed = 0
    $index = 0
    foreach ($url in $urls) {
        $index++
        $prefix = ('Episode_{0:D3}' -f $index)
        Write-Host ''
        Write-Host ("[{0}/{1}] Processing {2}" -f $index, $urls.Count, $prefix) -ForegroundColor Cyan
        $downloadArgs = @(
            '--ignore-config', '--no-playlist', '--continue', '--no-overwrites',
            '--windows-filenames', '--download-archive', $archiveFile,
            '-P', $seriesDir,
            '-o', ($prefix + ' - %(title).100B [%(id)s].%(ext)s'),
            '--', $url
        )
        & $ytDlp @downloadArgs
        if ($LASTEXITCODE -eq 0) { $ok++ } else { $failed++ }
    }
    Write-Host ''
    Write-Host "Processed successfully or previously archived: $ok / $($urls.Count)" -ForegroundColor Green
    if ($failed -gt 0) { Write-Host "Failed links: $failed (check messages above)" -ForegroundColor Yellow }
    Write-Host "Files: $seriesDir"
    Finish $(if ($failed -eq 0) { 0 } else { 3 })
}

if ([string]::IsNullOrWhiteSpace($VideoUrl)) {
    $VideoUrl = Read-Host 'Paste an accessible video or supported playlist page URL'
}
$VideoUrl = ([string]$VideoUrl).Trim().Trim('"')
if (-not (Test-HttpUrl $VideoUrl)) {
    Write-Host 'Invalid URL. Use http:// or https://' -ForegroundColor Red
    Finish 1
}

if (($action -eq '3') -and ($VideoUrl -match '(?i)\.m3u8(?:[?#]|$)')) {
    Write-Host ''
    Write-Host 'This .m3u8 URL is a stream for ONE video, not a series episode list.' -ForegroundColor Yellow
    Write-Host 'Use action 2 for one video, or action 4 with episodes.txt for a series.'
    Write-Host 'A series page URL works with action 3 only if yt-dlp recognizes it as a playlist.'
    Finish 1
}

$playlistOption = if ($action -eq '2') { '--no-playlist' } else { '--yes-playlist' }
$previewArgs = @('--ignore-config', $playlistOption, '--simulate', '--skip-download',
                 '--print', '%(playlist_index)s | %(title)s [%(id)s]', '--', $VideoUrl)
if ($action -eq '3') { $previewArgs = @('--playlist-end', '5') + $previewArgs }
Write-Host ''
Write-Host 'Checking the input (no download yet)...' -ForegroundColor Cyan
& $ytDlp @previewArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host 'The URL is not supported or currently accessible to this tool.' -ForegroundColor Yellow
    Finish 2
}
if ($action -eq '1') { Write-Host 'Inspection complete. No files saved.'; Finish 0 }
if (-not (Confirm-Permission)) { Write-Host 'Cancelled.'; Finish 0 }

$outputDir = if ($action -eq '3') { Choose-SeriesFolder $downloadRoot } else { $downloadRoot }
if ($action -eq '3') { $archiveFile = Join-Path $outputDir 'downloaded_ids.txt' }
$outputName = if ($action -eq '3') {
    '%(playlist_index)s - %(title).100B [%(id)s].%(ext)s'
} else {
    '%(title).150B [%(id)s].%(ext)s'
}
$downloadArgs = @(
    '--ignore-config', $playlistOption, '--continue', '--no-overwrites',
    '--windows-filenames', '--download-archive', $archiveFile,
    '-P', $outputDir, '-o', $outputName, '--', $VideoUrl
)
Write-Host ''
Write-Host "Downloading into: $outputDir" -ForegroundColor Cyan
& $ytDlp @downloadArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Download failed or partially completed. Review output above.' -ForegroundColor Yellow
    Finish 3
}
Write-Host 'Finished. Already-downloaded items may have been skipped.' -ForegroundColor Green
if ($action -eq '3') {
    Write-Host 'Note: If only one file appeared, the source was not a multi-episode playlist.' -ForegroundColor Yellow
}
Finish 0
