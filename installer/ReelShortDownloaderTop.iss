#define AppName "Reel Short Downloader Top"
#define AppVersion "0.2.2"
#define AppExe "ReelShortDownloaderTop.exe"
[Setup]
AppId={{66DB0E5E-A66A-4DBD-B30B-7C777D9A00B1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Reel Short Downloader Top
DefaultDirName={localappdata}\Programs\ReelShortDownloaderTop
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=ReelShortDownloaderTop-Setup-{#AppVersion}-win-x64
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
[Files]
Source: "..\dist\ReelShortDownloaderTop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
[Run]
Filename: "{app}\{#AppExe}"; Description: "Open Reel Short Downloader Top"; Flags: nowait postinstall skipifsilent
[Code]
var
  RuntimeReady: Boolean;
  RequirementsPage: TWizardPage;
  RequirementsText: TNewStaticText;

procedure ExitIncomplete(Sender: TObject);
begin
  if not RuntimeReady then PostMessage(0, $0012, 12, 0);
end;

function CheckRequirements(): Boolean;
var Code: Integer;
begin
  Result := Exec(ExpandConstant('{app}\{#AppExe}'), '--check-runtime --report "' + ExpandConstant('{localappdata}\ReelShortDownloaderTop\installer-runtime.json') + '"', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
  RuntimeReady := Result and (Code = 0);
  Result := RuntimeReady;
end;

procedure InitializeWizard();
begin
  RuntimeReady := False;
  RequirementsPage := CreateCustomPage(wpInstalling, 'Check requirements', 'The application includes its download tools.');
  RequirementsText := TNewStaticText.Create(RequirementsPage);
  RequirementsText.Parent := RequirementsPage.Surface;
  RequirementsText.Width := RequirementsPage.SurfaceWidth;
  RequirementsText.Height := ScaleY(100);
  RequirementsText.WordWrap := True;
  RequirementsText.Caption := 'A requirement did not pass. Install Microsoft Edge if missing, then click Retry. Browser extension setup is available from button 2 in the application.';
end;

procedure CurStepChanged(Step: TSetupStep);
begin
  if Step = ssPostInstall then CheckRequirements();
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = RequirementsPage.ID) and RuntimeReady;
end;

procedure CurPageChanged(PageID: Integer);
begin
  if PageID = RequirementsPage.ID then
  begin
    WizardForm.NextButton.Caption := 'Retry';
    WizardForm.CancelButton.Enabled := True;
    WizardForm.CancelButton.Visible := True;
    WizardForm.CancelButton.OnClick := @ExitIncomplete;
    if WizardSilent then PostMessage(0, $0012, 12, 0);
  end;
end;

function NextButtonClick(PageID: Integer): Boolean;
begin
  Result := True;
  if PageID = RequirementsPage.ID then Result := CheckRequirements();
  if PageID = wpFinished then Result := RuntimeReady;
end;

function GetCustomSetupExitCode(): Integer;
begin
  if RuntimeReady then Result := 0 else Result := 12;
end;
