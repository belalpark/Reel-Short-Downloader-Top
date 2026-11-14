$ErrorActionPreference = 'Stop'
$installer = Get-ChildItem (Join-Path $PSScriptRoot '..\release') -Filter '*Setup*.exe' | Select-Object -First 1
if (-not $installer) { throw 'Installer not found' }
$testDir = Join-Path $env:RUNNER_TEMP 'ReelShortInstallerTest'
$log = Join-Path $env:RUNNER_TEMP 'ReelShortInstaller.log'
$evidenceDir = Join-Path $PSScriptRoot '..\test-results'
New-Item -ItemType Directory -Force $evidenceDir | Out-Null
# Check the packaged application's refusal separately from the installer's early gate.
$app = (Resolve-Path (Join-Path $PSScriptRoot '..\dist\ReelShortDownloaderTop\ReelShortDownloaderTop.exe')).Path
$readiness = Join-Path $evidenceDir 'installer-readiness.json'
$check = Start-Process -FilePath $app -ArgumentList ('--verify-install --report "' + $readiness + '"') -PassThru
if (-not $check.WaitForExit(60000)) { Stop-Process -Id $check.Id -Force; throw 'Readiness check timed out' }
if ($check.ExitCode -eq 0) { throw 'Packaged application falsely accepted an unverified install' }
$report = Get-Content -LiteralPath $readiness -Raw | ConvertFrom-Json
$activation = @($report.checks | Where-Object { $_.name -eq 'Live Edge activation' })
if ($activation.Count -ne 1 -or $activation[0].ok) { throw 'Expected activation failure was not recorded' }
$arguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/DIR="' + $testDir + '"'),('/LOG="' + $log + '"'))
$process = Start-Process -FilePath $installer.FullName -ArgumentList $arguments -PassThru
try {
    if (-not $process.WaitForExit(60000)) { Stop-Process -Id $process.Id -Force; throw 'Silent installer did not exit promptly' }
    if ($process.ExitCode -eq 0) { throw 'Installer falsely reported success without an activated extension' }
    if (Test-Path (Join-Path $testDir 'ReelShortDownloaderTop.exe')) { throw 'Silent installer copied files before refusing interactive activation' }
    if (-not (Select-String -LiteralPath $log -SimpleMatch 'Activation gate: interactive Edge verification required.' -Quiet)) { throw 'Installer did not record the reason for refusal' }
    @{exitCode=$process.ExitCode; activationRejected=$true; installedFilesPresent=$false; packagedReadinessExitCode=$check.ExitCode} | ConvertTo-Json | Set-Content (Join-Path $evidenceDir 'installer-negative.json')
} finally {
    if (Test-Path $log) { Copy-Item -LiteralPath $log -Destination (Join-Path $evidenceDir 'installer-negative.log') }
}
