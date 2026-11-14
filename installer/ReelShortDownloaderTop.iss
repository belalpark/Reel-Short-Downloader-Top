#define AppName "Reel Short Downloader Top"
#define AppVersion "0.1.0"
#define AppExe "ReelShortDownloaderTop.exe"

[Setup]
AppId={{66DB0E5E-A66A-4DBD-B30B-7C777D9A00B1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Reel Short Downloader Top
AppPublisherURL=https://github.com/belalpark/Reel-Short-Downloader-Top
DefaultDirName={localappdata}\Programs\ReelShortDownloaderTop
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
DisableProgramGroupPage=yes
DisableWelcomePage=no
OutputDir=..\release
OutputBaseFilename=ReelShortDownloaderTop-Setup-{#AppVersion}-win-x64
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoDescription={#AppName} Setup

[Files]
Source: "..\dist\ReelShortDownloaderTop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Set up Microsoft Edge"; Filename: "{app}\{#AppExe}"; Parameters: "--setup"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open Reel Short Downloader Top"; Flags: nowait postinstall skipifsilent

[Code]
var
  ActivationVerified: Boolean;

procedure InitializeWizard();
begin
  ActivationVerified := False;
  WizardForm.WelcomeLabel2.Caption :=
    'This installer includes the application, Python runtime, yt-dlp and FFmpeg.' + #13#10#13#10 +
    'After files are copied, the setup window opens. Activate the bundled extension in Microsoft Edge and pair it with the displayed code.' + #13#10#13#10 +
    'Setup will not report success or show Finish until the runtime checks and a live Edge connection pass. Closing the setup window leaves installation incomplete.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Parameters: String;
begin
  if CurStep = ssPostInstall then
  begin
    if WizardSilent then
      Parameters := '--verify-install --report "' + ExpandConstant('{localappdata}\ReelShortDownloaderTop\installer-readiness.json') + '"'
    else
      Parameters := '--setup';
    WizardForm.StatusLabel.Caption := 'Checking the runtime and waiting for Microsoft Edge activation...';
    if not Exec(ExpandConstant('{app}\{#AppExe}'), Parameters, ExpandConstant('{app}'), SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then
      RaiseException('Readiness verification could not start. Setup is incomplete.');
    if ResultCode <> 0 then
      RaiseException('Readiness verification did not pass (code ' + IntToStr(ResultCode) + '). Setup is incomplete. Re-run the interactive installer to finish activation.');
    ActivationVerified := True;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpFinished then
    Result := ActivationVerified;
end;

function GetCustomSetupExitCode(): Integer;
begin
  if ActivationVerified then Result := 0 else Result := 12;
end;
