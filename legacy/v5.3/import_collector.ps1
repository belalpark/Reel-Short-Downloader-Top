param([string]$CsvPath)
# Drama Archiver v5: download from explicit media URLs published in CSV exported by the Chrome helper.
# No DRM decryption, paywall unlocking, private API scanning, or guessing episode URLs.
$ErrorActionPreference = 'Stop'

function Test-HttpUrl([string]$Value) {
    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri)) { return $false }
    return ($uri.Scheme -eq 'https' -or $uri.Scheme -eq 'http')
}

function Get-ValidatedMediaUrl([string]$Value) {
    $raw = ([string]$Value).Trim().Trim('"').Trim("'")
    if ([string]::IsNullOrWhiteSpace($raw)) { return '' }
    if ($raw -match '(?i)^(undefined|null|none|nan|\[object object\])$') { return '' }
    $uri = $null
    if (-not [Uri]::TryCreate($raw, [UriKind]::Absolute, [ref]$uri)) { return '' }
    if ($uri.Scheme -notin @('https', 'http')) { return '' }
    if (-not $uri.Host -or $uri.AbsolutePath -match '(?i)(?:^|/)(undefined|null|none|nan)(?:/|$)') { return '' }
    # In a collector export MediaURL must be a known media format, never a ReelShort page.
    if ($uri.AbsolutePath -notmatch '(?i)\.(m3u8|mp4|m4v|webm|mov|mpd)$') { return '' }
    return $uri.AbsoluteUri
}
function Safe-Name([string]$Name) {
    $s = [regex]::Replace($Name, '[<>:"/\\|?*\x00-\x1F]', '_').Trim([char[]]@(' ', '.'))
    if ([string]::IsNullOrWhiteSpace($s)) { $s = 'Series' }
    if ($s -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') { $s = '_' + $s }
    if ($s.Length -gt 65) { $s = $s.Substring(0,65) }
    return $s
}
function Ensure-Directory([string]$Directory) {
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        [void](New-Item -ItemType Directory -Force -Path $Directory)
    }
}
function Get-YtDlp {
    $local = Join-Path $PSScriptRoot 'yt-dlp.exe'
    if (Test-Path -LiteralPath $local -PathType Leaf) { return $local }
    $command = Get-Command yt-dlp -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return ''
}
Write-Host '-------------------------------------------------------' -ForegroundColor Cyan
Write-Host ' Drama Archiver v5 | Import Chrome Collector CSV' -ForegroundColor Cyan
Write-Host '-------------------------------------------------------' -ForegroundColor Cyan
Write-Host 'CSV contains observed episode page URLs and available video URLs.' -ForegroundColor Gray
Write-Host 'An active subscription does not necessarily grant permission to save files.' -ForegroundColor Yellow
Write-Host ''
# Accept the CSV file itself, its containing folder (including a folder called csv),
# or an Explorer display-path where Arabic "سطح المكتب" means Desktop.
function Get-CandidatePaths([string]$RawPath) {
    $paths = New-Object 'System.Collections.Generic.List[string]'
    $clean = ([string]$RawPath).Trim().Trim('"').Trim("'")
    $desktop = [Environment]::GetFolderPath('Desktop')
    if ($clean) {
        [void]$paths.Add($clean)
        if ($clean.Contains('سطح المكتب')) {
            [void]$paths.Add($clean.Replace('سطح المكتب', 'Desktop'))
            if ($desktop) {
                $parts = $clean -split '[\\/]'
                $pos = [Array]::IndexOf($parts, 'سطح المكتب')
                if ($pos -ge 0) {
                    $after = @($parts | Select-Object -Skip ($pos + 1))
                    $relative = $after -join [IO.Path]::DirectorySeparatorChar
                    if ($relative) { [void]$paths.Add((Join-Path $desktop $relative)) }
                    else { [void]$paths.Add($desktop) }
                }
            }
        }
        if (-not [IO.Path]::IsPathRooted($clean)) {
            [void]$paths.Add((Join-Path $PSScriptRoot $clean))
            if ($desktop) { [void]$paths.Add((Join-Path $desktop $clean)) }
        }
    } else {
        if ($desktop) {
            [void]$paths.Add((Join-Path $desktop 'csv'))
            [void]$paths.Add($desktop)
        }
        if ($env:OneDrive) {
            [void]$paths.Add((Join-Path $env:OneDrive 'Desktop\csv'))
            [void]$paths.Add((Join-Path $env:OneDrive 'Desktop'))
        }
        [void]$paths.Add($PSScriptRoot)
    }
    return @($paths | Select-Object -Unique)
}
function Find-CsvInPath([string]$InputPath) {
    foreach ($candidate in @(Get-CandidatePaths $InputPath)) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            if ([IO.Path]::GetExtension($candidate) -ieq '.csv') {
                return (Get-Item -LiteralPath $candidate).FullName
            }
        }
        if (-not (Test-Path -LiteralPath $candidate -PathType Container)) { continue }
        $preferred = Join-Path $candidate 'drama_episode_index.csv'
        if (Test-Path -LiteralPath $preferred -PathType Leaf) { return (Get-Item -LiteralPath $preferred).FullName }
        $allCsv = @(Get-ChildItem -LiteralPath $candidate -File -Filter '*.csv' -ErrorAction SilentlyContinue)
        if ($allCsv.Count -eq 1) { return $allCsv[0].FullName }
        if ($allCsv.Count -gt 1) {
            Write-Host "Multiple CSV files found in: $candidate" -ForegroundColor Yellow
            for ($i=0; $i -lt $allCsv.Count; $i++) {
                Write-Host ('  {0} = {1}' -f ($i+1),$allCsv[$i].Name)
            }
            $pick = Read-Host 'Choose file number (Enter = try another path)'
            $number = 0
            if ([int]::TryParse($pick,[ref]$number) -and $number -ge 1 -and $number -le $allCsv.Count) {
                return $allCsv[$number-1].FullName
            }
        }
    }
    return $null
}
# v5: Never silently select an old desktop export when the user presses Enter.
# List fresh scanner exports for explicit choice.
if (-not $CsvPath) {
    $userDownloads = Join-Path $env:USERPROFILE 'Downloads'
    $recent = @()
    $scannerRoot = Join-Path $userDownloads 'Drama_Scanner'
    if (Test-Path -LiteralPath $scannerRoot -PathType Container) {
        $recent += @(Get-ChildItem -LiteralPath $scannerRoot -Recurse -File -Filter 'series_index.csv' -ErrorAction SilentlyContinue)
    }
    if (Test-Path -LiteralPath $userDownloads -PathType Container) {
        $recent += @(Get-ChildItem -LiteralPath $userDownloads -File -Filter 'Drama_*.csv' -ErrorAction SilentlyContinue)
    }
    if ($recent.Count -gt 0) {
        $recent = @($recent | Sort-Object LastWriteTime -Descending | Select-Object -Unique -First 15)
        Write-Host ''
        Write-Host 'Recent scanner CSV files (choose one; old CSV files are not auto-selected):' -ForegroundColor Cyan
        for ($i=0; $i -lt $recent.Count; $i++) {
            Write-Host ('  {0} = {1} | {2}' -f ($i+1),$recent[$i].FullName,$recent[$i].LastWriteTime)
        }
        $pickCsv = Read-Host 'Enter the CSV number, or paste a full CSV path; Enter = original search'
        $pickNumber = 0
        if ([int]::TryParse($pickCsv,[ref]$pickNumber) -and $pickNumber -ge 1 -and $pickNumber -le $recent.Count) {
            $CsvPath = $recent[$pickNumber-1].FullName
        } elseif ($pickCsv) { $CsvPath = $pickCsv }
    }
}
Write-Host 'You may paste a CSV FILE path OR its FOLDER path (such as ...\Desktop\csv).' -ForegroundColor Cyan
Write-Host 'Press Enter for automatic Desktop search, or type B to return to the menu.' -ForegroundColor Gray
$attempt = 0
while ($true) {
    if (-not $CsvPath -and $attempt -eq 0) { $CsvPath = Read-Host 'CSV file or folder (Enter = search Desktop)' }
    $CsvPath = ([string]$CsvPath).Trim().Trim('"').Trim("'")
    if ($CsvPath -ieq 'B') { Write-Host 'Returning to main menu.'; exit 0 }
    $found = Find-CsvInPath $CsvPath
    if ($found) { $CsvPath = $found; break }
    Write-Host "No CSV file found at: $CsvPath" -ForegroundColor Yellow
    Write-Host 'Expected name: drama_episode_index.csv' -ForegroundColor Yellow
    Write-Host ('Actual Windows Desktop folder: ' + [Environment]::GetFolderPath('Desktop')) -ForegroundColor Cyan
    Write-Host 'Tip: In File Explorer, Shift+right-click the CSV file > Copy as path.' -ForegroundColor Gray
    $CsvPath = Read-Host 'Paste actual CSV file/folder path, B = menu, Enter = auto search'
    $attempt++
}
Write-Host ("CSV selected: " + $CsvPath) -ForegroundColor Green
$rows = @(Import-Csv -LiteralPath $CsvPath -Encoding UTF8)
if ($rows.Count -eq 0) { Write-Host 'CSV has no rows.' -ForegroundColor Yellow; exit 2 }
$needed = @('SeriesID','SeriesTitle','Episode','PageURL','MediaURL')
$cols = @($rows[0].PSObject.Properties.Name)
foreach ($field in $needed) { if ($cols -notcontains $field) { throw "Missing CSV column: $field" } }

$groups = @($rows | Group-Object -Property SeriesID)
Write-Host 'Found series:' -ForegroundColor Cyan
for ($i=0; $i -lt $groups.Count; $i++) {
    $g = $groups[$i]
    $title = [string]$g.Group[0].SeriesTitle
    $available = @($g.Group | Where-Object { Get-ValidatedMediaUrl ([string]$_.MediaURL) }).Count
    Write-Host (" {0} = {1} | indexed {2} | video URLs {3}" -f ($i + 1),$title,$g.Count,$available)
}
if ($groups.Count -gt 1) {
    $choice = Read-Host 'Enter series number (Enter = 1)'
    if (-not $choice) { $choice = '1' }
    $chosenIndex = 0
    if (-not [int]::TryParse($choice, [ref]$chosenIndex) -or $chosenIndex -lt 1 -or $chosenIndex -gt $groups.Count) { throw 'Invalid series number.' }
    $group = $groups[$chosenIndex - 1]
} else { $group = $groups[0] }

$chosen = @($group.Group | Sort-Object -Property { [int]$_.Episode })
$entriesByEpisode = @{}
$invalid = 0
$pageOnly = 0
foreach ($row in $chosen) {
    $ep = 0
    if (-not [int]::TryParse([string]$row.Episode, [ref]$ep)) { $invalid++; continue }
    if ($ep -lt 1 -or $ep -gt 9999) { $invalid++; continue }
    $url = Get-ValidatedMediaUrl ([string]$row.MediaURL)
    if (-not $url) {
        if ([string]::IsNullOrWhiteSpace([string]$row.MediaURL)) { $pageOnly++ }
        else { $invalid++ }
        continue
    }
    # A later valid row may replace another valid row for the same episode,
    # but an invalid row can never block a valid candidate.
    $entriesByEpisode[$ep] = [PSCustomObject]@{
        Episode=$ep; MediaURL=$url; PageURL=[string]$row.PageURL
    }
}
$entries = @($entriesByEpisode.Values | Sort-Object -Property Episode)
Write-Host "CSV rows: $($chosen.Count) | valid media episodes: $($entries.Count) | invalid media values: $invalid | page-only rows: $pageOnly" -ForegroundColor Cyan
if ($invalid -gt 0) {
    Write-Host 'Invalid entries such as https://www.reelshort.com/undefined were SKIPPED.' -ForegroundColor Yellow
}
if ($pageOnly -gt 0) {
    Write-Host 'Page-only entries have NO known video URL. They will NOT be downloaded.' -ForegroundColor Yellow
}
if ($entries.Count -eq 0) {
    Write-Host 'Nothing to download: open episode pages and export CSV again, or use episodes.txt with links you are authorized to save.' -ForegroundColor Yellow
    exit 3
}
$ytDlp = Get-YtDlp
if (-not $ytDlp) {
    Write-Host 'yt-dlp not found. Install it using:' -ForegroundColor Yellow
    Write-Host 'winget install --source winget --id yt-dlp.yt-dlp'
    exit 4
}
Write-Host ''
Write-Host 'Continue only if you have permission to download and retain these specific videos.' -ForegroundColor Yellow
$confirm = Read-Host 'Type YES to continue'
if ($confirm -cne 'YES') { Write-Host 'Cancelled.'; exit 0 }
Write-Host ''
Write-Host 'Recommendation: press Enter (NO browser cookies). Public/direct video links often work without them.' -ForegroundColor Cyan
Write-Host 'Browser cookie mode is optional. An Edge/Chrome database lock can cause a failure.' -ForegroundColor Yellow
$browser = Read-Host 'Optional browser cookies [Enter=none / edge / chrome]'
$browser = ([string]$browser).Trim().ToLowerInvariant()
if ($browser -notin @('edge','chrome')) { $browser = '' }
if ($browser) {
    Write-Host 'Cookies are read locally and used only for yt-dlp requests. Do not share cookie files or logs.' -ForegroundColor Yellow
}
$root = Join-Path $PSScriptRoot 'Downloads'
Ensure-Directory $root
$seriesFolder = Join-Path $root (Safe-Name ([string]$group.Group[0].SeriesTitle))
Ensure-Directory $seriesFolder
$archive = Join-Path $seriesFolder 'downloaded_ids.txt'
$ok = 0; $failed = 0
$index = 0
foreach ($entry in $entries) {
    $index++
    $prefix = ('Episode_{0:D3}' -f $entry.Episode)
    Write-Host "`n[$index/$($entries.Count)] $prefix" -ForegroundColor Cyan
    $arguments = @('--ignore-config','--no-playlist','--continue','--no-overwrites','--windows-filenames',
                  '--retries','3','--fragment-retries','3','--download-archive',$archive,
                  '-P',$seriesFolder,'-o',($prefix + ' - %(title).80B [%(id)s].%(ext)s'))
    if (Test-HttpUrl $entry.PageURL) { $arguments += @('--referer',$entry.PageURL) }
    $arguments += @('--',$entry.MediaURL)
    if ($browser) {
        Write-Host "Attempting using optional $browser cookies..." -ForegroundColor DarkGray
        $cookieArgs = @('--cookies-from-browser',$browser) + $arguments
        & $ytDlp @cookieArgs
        $exit = $LASTEXITCODE
        if ($exit -ne 0) {
            Write-Host 'Cookie-based attempt failed. Retrying this file WITHOUT browser cookies.' -ForegroundColor Yellow
            Write-Host 'Browser cookies will be disabled for the rest of this batch.' -ForegroundColor Yellow
            $browser = ''
            & $ytDlp @arguments
            $exit = $LASTEXITCODE
        }
    } else {
        & $ytDlp @arguments
        $exit = $LASTEXITCODE
    }
    if ($exit -eq 0) { $ok++ } else { $failed++ }
}
Write-Host ''
Write-Host "Completed or already archived: $ok; failed: $failed" -ForegroundColor Cyan
Write-Host "Destination: $seriesFolder" -ForegroundColor Green
Write-Host 'Failures can be caused by expired links, unsupported formats, authentication or DRM.' -ForegroundColor Gray
exit $(if ($failed -eq 0) { 0 } else { 5 })
