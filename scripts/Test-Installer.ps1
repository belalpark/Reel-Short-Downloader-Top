$ErrorActionPreference = 'Stop'
$installer = Get-ChildItem (Join-Path $PSScriptRoot '..\release') -Filter '*Setup*.exe' | Select-Object -First 1
if (-not $installer) { throw 'Installer not found' }
$testDir = Join-Path $env:RUNNER_TEMP 'ReelShortInstallerTest'
$log = Join-Path $env:RUNNER_TEMP 'ReelShortInstaller.log'
$evidenceDir = Join-Path $PSScriptRoot '..\test-results'
New-Item -ItemType Directory -Force $evidenceDir | Out-Null
function Wait-Checked($process, $description) {
    if (-not $process.WaitForExit(90000)) { Stop-Process -Id $process.Id -Force; throw ($description + ' timed out') }
    return $process.ExitCode
}
$arguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/DIR="' + $testDir + '"'),('/LOG="' + $log + '"'))
try {
    $process = Start-Process -FilePath $installer.FullName -ArgumentList $arguments -PassThru
    $installCode = Wait-Checked $process 'Silent installer'
    if ($installCode -ne 0) { throw ('Healthy installer failed: ' + $installCode) }
    $app = Join-Path $testDir 'ReelShortDownloaderTop.exe'
    if (-not (Test-Path $app)) { throw 'Installed application is missing' }
    $readiness = Join-Path $evidenceDir 'installed-runtime.json'
    $process = Start-Process -FilePath $app -ArgumentList ('--verify-install --report "' + $readiness + '"') -PassThru
    $verifyCode = Wait-Checked $process 'Installed requirements check'
    $report = Get-Content -LiteralPath $readiness -Raw | ConvertFrom-Json
    if ($verifyCode -ne 0 -or @($report.checks | Where-Object { -not $_.ok }).Count) { throw 'Installed requirements were not healthy' }
    $tool = Join-Path $testDir 'tools\ffmpeg.exe'
    Move-Item -LiteralPath $tool -Destination ($tool + '.test-backup')
    try {
        $brokenReport = Join-Path $evidenceDir 'installed-missing-tool.json'
        $process = Start-Process -FilePath $app -ArgumentList ('--check-runtime --report "' + $brokenReport + '"') -PassThru
        $brokenCode = Wait-Checked $process 'Missing dependency check'
        if ($brokenCode -ne 12) { throw 'Missing dependency was falsely accepted' }
        $broken = Get-Content -LiteralPath $brokenReport -Raw | ConvertFrom-Json
        if (@($broken.checks | Where-Object { $_.name -eq 'Package integrity' -and -not $_.ok }).Count -ne 1) { throw 'Missing dependency was not reported' }
    } finally { Move-Item -LiteralPath ($tool + '.test-backup') -Destination $tool }
    $uninstall = Join-Path $testDir 'unins000.exe'
    $process = Start-Process -FilePath $uninstall -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -PassThru
    $uninstallCode = Wait-Checked $process 'Uninstaller'
    if ($uninstallCode -ne 0 -or (Test-Path $app)) { throw 'Uninstaller failed to remove the application' }
    @{installExitCode=$installCode; runtimeExitCode=$verifyCode; missingToolExitCode=$brokenCode; uninstallExitCode=$uninstallCode; activationRequiredDuringInstall=$false} | ConvertTo-Json | Set-Content (Join-Path $evidenceDir 'installer.json')
} finally {
    if (Test-Path $log) { Copy-Item -LiteralPath $log -Destination (Join-Path $evidenceDir 'installer.log') }
}
