$ErrorActionPreference = 'Stop'
$installer = Get-ChildItem (Join-Path $PSScriptRoot '..\release') -Filter '*Setup*.exe' | Select-Object -First 1
if (-not $installer) { throw 'Installer not found' }
$testDir = Join-Path $env:RUNNER_TEMP 'ReelShortInstallerTest'
$log = Join-Path $env:RUNNER_TEMP 'ReelShortInstaller.log'
$arguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/DIR="' + $testDir + '"'),('/LOG="' + $log + '"'))
$process = Start-Process -FilePath $installer.FullName -ArgumentList $arguments -Wait -PassThru
if ($process.ExitCode -eq 0) { throw 'Installer falsely reported success without an activated extension' }
$app = Join-Path $testDir 'ReelShortDownloaderTop.exe'
if (-not (Test-Path $app)) { throw 'Installer failed before exercising its activation gate' }
$readiness = Join-Path $env:LOCALAPPDATA 'ReelShortDownloaderTop\installer-readiness.json'
if (-not (Test-Path $readiness)) { throw 'No installer readiness report was written' }
$report = Get-Content -LiteralPath $readiness -Raw | ConvertFrom-Json
$activation = @($report.checks | Where-Object { $_.name -eq 'Live Edge activation' })
if ($activation.Count -ne 1 -or $activation[0].ok) { throw 'Expected live activation failure was not recorded' }
$evidenceDir = Join-Path $PSScriptRoot '..\test-results'
New-Item -ItemType Directory -Force $evidenceDir | Out-Null
@{exitCode=$process.ExitCode; activationRejected=$true; installedFilesPresent=$true} | ConvertTo-Json | Set-Content (Join-Path $evidenceDir 'installer-negative.json')
Copy-Item -LiteralPath $log -Destination (Join-Path $evidenceDir 'installer-negative.log')
$uninstaller = Join-Path $testDir 'unins000.exe'
if (-not (Test-Path $uninstaller)) { throw 'Uninstaller was not created' }
$remove = Start-Process -FilePath $uninstaller -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -Wait -PassThru
if ($remove.ExitCode -ne 0 -or (Test-Path $app)) { throw 'Uninstall test failed' }
