param([string]$InputPath)
# Series metadata extractor for locally saved HTML. PowerShell 5.1+.
# Reads disk files ONLY; never makes web requests and never bypasses paywall/DRM.
$ErrorActionPreference = 'Stop'

function Safe-Name([string]$Value) {
  $s = [regex]::Replace($Value, '[<>:"/\\|?*\x00-\x1F]', '_').Trim([char[]]@(' ', '.'))
  if (-not $s) { $s = 'Unnamed_Series' }
  if ($s -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') { $s = '_' + $s }
  if ($s.Length -gt 70) { $s = $s.Substring(0,70) }
  return $s
}
function Absolute-Link([string]$href) {
  $href = [System.Net.WebUtility]::HtmlDecode($href)
  if ($href -match '^https?://') { return $href }
  if ($href.StartsWith('/')) { return "https://www.reelshort.com$href" }
  return ''
}
function File-Text([string]$path) { return [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8) }

Write-Host '----------------------------------------------------------------' -ForegroundColor Cyan
Write-Host '  DRAMA ARCHIVER v4.3 | OFFLINE SERIES HTML INSPECTOR' -ForegroundColor Cyan
Write-Host '----------------------------------------------------------------' -ForegroundColor Cyan
Write-Host 'Metadata inspection only: no login, network calls, or downloads.' -ForegroundColor Gray
Write-Host ''
if (-not $InputPath) { $InputPath = Read-Host 'Path to saved HTML/TXT file or a folder of HTML files' }
$InputPath = $InputPath.Trim().Trim('"')
if (-not (Test-Path -LiteralPath $InputPath)) {
  Write-Host 'File or folder not found.' -ForegroundColor Red
  exit 1
}
$files = @()
if (Test-Path -LiteralPath $InputPath -PathType Container) {
  $files = @(Get-ChildItem -LiteralPath $InputPath -File | Where-Object { $_.Extension -in @('.html','.htm','.txt') })
} else {
  $files = @(Get-Item -LiteralPath $InputPath)
}
if ($files.Count -eq 0) { Write-Host 'No HTML/TXT files found.' -ForegroundColor Yellow; exit 2 }

$series = @{}
$seenFiles = 0
foreach ($file in $files) {
  try {
    $html = File-Text $file.FullName
    $match = [regex]::Match($html, '(?is)<script\b[^>]*\bid=["'']__NEXT_DATA__["''][^>]*>\s*(.*?)\s*</script>')
    if (-not $match.Success) {
      Write-Host "Ignored (no __NEXT_DATA__): $($file.Name)" -ForegroundColor DarkGray
      continue
    }
    $root = ConvertFrom-Json -InputObject $match.Groups[1].Value
    $d = $root.props.pageProps.data
    if ($null -eq $d -or [string]::IsNullOrWhiteSpace([string]$d.book_title)) {
      Write-Host "Ignored (no series metadata): $($file.Name)" -ForegroundColor DarkGray
      continue
    }
    # Browser "Save as HTML" can capture a stale Next.js hydration payload
    # after client-side navigation. Do NOT label an Arabic show with another
    # show's hidden metadata.
    $pageTitleMatch = [regex]::Match($html, '(?is)<title\b[^>]*>\s*(.*?)\s*</title>')
    if ($pageTitleMatch.Success) {
      $displayedTitle = [System.Net.WebUtility]::HtmlDecode($pageTitleMatch.Groups[1].Value).Trim()
      $dataTitle = ([string]$d.book_title).Trim()
      if ($dataTitle -and $displayedTitle.IndexOf($dataTitle, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        Write-Host "SKIPPED stale/mismatched page: $($file.Name)" -ForegroundColor Yellow
        Write-Host "  Visible title: $displayedTitle" -ForegroundColor Yellow
        Write-Host "  Hydration series: $dataTitle" -ForegroundColor Yellow
        continue
      }
    }
    # A foreign-language saved filename paired with completely different
    # embedded-language series data may indicate a stale or incorrectly saved page.
    if ($file.BaseName -match '[\u0600-\u06FF]' -and [string]$d.book_title -notmatch '[\u0600-\u06FF]') {
      Write-Host "SKIPPED possible wrong/stale HTML: Arabic filename contains English-only series data: $($file.Name)" -ForegroundColor Yellow
      Write-Host "  Embedded series: $($d.book_title)" -ForegroundColor Yellow
      Write-Host '  Please re-save the correct episode after a full page refresh.' -ForegroundColor Yellow
      continue
    }
    $seenFiles++
    $id = [string]$d.book_id
    if (-not $id) { $id = [string]$d.book_title }
    if (-not $series.ContainsKey($id)) {
      $series[$id] = [PSCustomObject]@{
        Id = $id; Title = [string]$d.book_title; Count = [int]$d.chapter_count;
        PaidStart = [int]$d.paid_start; EpisodePages = @{}; FreeMedia = @{}; Files = @()
      }
    }
    $rec = $series[$id]
    if ([int]$d.chapter_count -gt $rec.Count) { $rec.Count = [int]$d.chapter_count }
    if ([int]$d.paid_start -gt 0) { $rec.PaidStart = [int]$d.paid_start }
    $rec.Files += $file.Name
    $ep = [int]$d.serial_number
    if ($ep -gt 0) {
      $canon = [regex]::Match($html, '(?is)<link\b[^>]*\brel=["'']canonical["''][^>]*\bhref=["'']([^"'']+)["'']')
      if ($canon.Success) { $rec.EpisodePages[$ep] = [System.Net.WebUtility]::HtmlDecode($canon.Groups[1].Value) }
      elseif ($root.query.slug) { $rec.EpisodePages[$ep] = 'https://www.reelshort.com/episodes/' + [string]$root.query.slug }
      # video_url represents the current page's video, not an episode list.
      if ($d.video_url -and ($rec.PaidStart -eq 0 -or $ep -lt $rec.PaidStart)) {
        $rec.FreeMedia[$ep] = [string]$d.video_url
      }
    }
    # Collect actual episode links in the saved HTML; never fabricate URLs from IDs.
    $links = [regex]::Matches($html, '(?is)\b(?:href)=["'']([^"'']*?/episodes/episode-(\d+)-[^"'']+)["'']')
    foreach ($link in $links) {
      $n = [int]$link.Groups[2].Value
      # Discard episode URLs that belong to a different series.
      if ($rec.Id -and $link.Groups[1].Value -notmatch [regex]::Escape($rec.Id)) { continue }
      if ($n -gt 0 -and (-not $rec.EpisodePages.ContainsKey($n))) {
        $resolved = Absolute-Link $link.Groups[1].Value
        if ($resolved) { $rec.EpisodePages[$n] = $resolved }
      }
    }
    Write-Host "Read: $($file.Name) | $($d.book_title) | episode $ep" -ForegroundColor Green
  } catch {
    Write-Host "Skipped unreadable file $($file.Name): $($_.Exception.Message)" -ForegroundColor Yellow
  }
}
if ($seenFiles -eq 0) {
  Write-Host 'No usable ReelShort episode metadata was found in the provided files.' -ForegroundColor Yellow
  exit 3
}

$rootOut = Join-Path $PSScriptRoot 'Downloads'
if (-not (Test-Path -LiteralPath $rootOut)) { [void](New-Item -ItemType Directory -Path $rootOut -Force) }
foreach ($id in @($series.Keys)) {
  $r = $series[$id]
  $folder = Join-Path $rootOut (Safe-Name $r.Title)
  if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Path $folder -Force) }
  $rows = New-Object System.Collections.Generic.List[object]
  for ($i=1; $i -le $r.Count; $i++) {
    $tier = if ($r.PaidStart -gt 0 -and $i -ge $r.PaidStart) { 'Paid_or_restricted_from_metadata' }
            elseif ($r.PaidStart -gt 0) { 'Before_paid_threshold_not_verified' }
            else { 'Unknown' }
    $pageUrl = if ($r.EpisodePages.ContainsKey($i)) { [string]$r.EpisodePages[$i] } else { '' }
    $mediaUrl = if ($tier -ne 'Paid_or_restricted_from_metadata' -and $r.FreeMedia.ContainsKey($i)) { [string]$r.FreeMedia[$i] } else { '' }
    $status = if ($mediaUrl) { 'Video_url_found_in_saved_html' } elseif ($pageUrl) { 'Episode_page_only' } else { 'No_url_in_saved_html' }
    $rows.Add([PSCustomObject]@{ Episode=$i; AccessTier=$tier; PageURL=$pageUrl; CandidateMediaURL=$mediaUrl; Evidence=$status })
  }
  $csv = Join-Path $folder 'series_index.csv'
  $rows | Export-Csv -LiteralPath $csv -NoTypeInformation -Encoding UTF8
  $candidate = Join-Path $folder 'candidate_free_video_urls.txt'
  @('# Video URLs shown in saved HTML for episodes before paid threshold.',
    '# Only use if downloading is permitted. Availability was NOT verified.',
    '# Media URLs for episodes at/after the pay threshold are excluded.') +
    @($rows | Where-Object { $_.CandidateMediaURL } | Sort-Object Episode | ForEach-Object { $_.CandidateMediaURL }) |
    Set-Content -LiteralPath $candidate -Encoding UTF8
  $metadata = Join-Path $folder 'series_summary.txt'
  @("Series: $($r.Title)", "Series ID: $($r.Id)", "Total episodes: $($r.Count)",
    "Paid access begins at episode: $($r.PaidStart)",
    "Episode page links found: $($r.EpisodePages.Count)",
    "Candidate video links found before pay threshold: $($r.FreeMedia.Count)",
    "Input HTML files: $($r.Files -join ', ')",
    'A full series is NOT derivable from a single HLS .m3u8 manifest.',
    'This tool cannot authenticate, bypass payment, bypass DRM, or grant copy rights.') |
    Set-Content -LiteralPath $metadata -Encoding UTF8
  Write-Host ''
  Write-Host "Series: $($r.Title)" -ForegroundColor Cyan
  Write-Host "Episodes: $($r.Count); paid begins: $($r.PaidStart)" -ForegroundColor Cyan
  Write-Host "Episode pages discovered: $($r.EpisodePages.Count); candidate free video URLs: $($r.FreeMedia.Count)"
  Write-Host "Index: $csv" -ForegroundColor Green
  Write-Host "Summary: $metadata" -ForegroundColor Green
  Write-Host "Candidate links: $candidate" -ForegroundColor Green
}
Write-Host ''
Write-Host 'Finished. This is an offline metadata inventory, NOT a series downloader.' -ForegroundColor Yellow
