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
  ActivationAttempted: Boolean;
  ActivationInProgress: Boolean;
  VerificationPage: TWizardPage;
  VerificationText: TNewStaticText;

function InitializeSetup(): Boolean;
begin
  Result := not WizardSilent;
  if not Result then
    Log('Activation gate: interactive Edge verification required. No files were installed.');
end;

procedure InitializeWizard();
begin
  ActivationVerified := False;
  ActivationAttempted := False;
  ActivationInProgress := False;
  WizardForm.WelcomeLabel2.Caption :=
    'This installer includes the application, Python runtime, yt-dlp and FFmpeg.' + #13#10#13#10 +
    'After files are copied, the setup window opens. Activate the bundled extension in Microsoft Edge and pair it with the displayed code.' + #13#10#13#10 +
    'Setup will not report success or show Finish until the runtime checks and a live Edge connection pass. Closing the setup window leaves installation incomplete.';
  VerificationPage := CreateCustomPage(wpInstalling, 'Verify Microsoft Edge',
    'Finish remains unavailable until every runtime check and a live Edge connection pass.');
  VerificationText := TNewStaticText.Create(VerificationPage);
  VerificationText.Parent := VerificationPage.Surface;
  VerificationText.Left := 0;
  VerificationText.Top := 0;
  VerificationText.Width := VerificationPage.SurfaceWidth;
  VerificationText.Height := ScaleY(160);
  VerificationText.WordWrap := True;
  VerificationText.Caption := 'The application setup window will open. Follow its Edge activation steps. If it is closed before verification, click Retry verification to try again, or Cancel to leave setup incomplete.';
end;

function RunActivation(): Boolean;
var
  ResultCode: Integer;
begin
  if ActivationInProgress then
  begin
    Result := False;
    Exit;
  end;
  ActivationInProgress := True;
  ActivationAttempted := True;
  WizardForm.NextButton.Enabled := False;
  WizardForm.CancelButton.Enabled := False;
  try
    Result := Exec(ExpandConstant('{app}\{#AppExe}'), '--setup',
      ExpandConstant('{app}'), SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode);
  finally
    ActivationInProgress := False;
    WizardForm.NextButton.Enabled := True;
    WizardForm.CancelButton.Enabled := True;
  end;
  ActivationVerified := Result and (ResultCode = 0);
  if ActivationVerified then
  begin
    VerificationText.Caption := 'Runtime checks and the paired Microsoft Edge connection passed. Click Next to complete installation.';
    WizardForm.NextButton.Caption := '&Next >';
  end
  else
  begin
    VerificationText.Caption := 'Verification did not pass. Setup is incomplete. Click Retry verification to reopen the application setup window, or Cancel to exit without reporting success.';
    WizardForm.NextButton.Caption := 'Retry verification >';
  end;
  Result := ActivationVerified;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = VerificationPage.ID then
  begin
    WizardForm.BackButton.Enabled := False;
    WizardForm.CancelButton.Visible := True;
    if not ActivationAttempted then RunActivation();
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = VerificationPage.ID then
  begin
    if not ActivationVerified then RunActivation();
    Result := ActivationVerified;
  end;
  if CurPageID = wpFinished then
    Result := ActivationVerified;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if (CurPageID = VerificationPage.ID) and not ActivationVerified then
  begin
    Cancel := True;
    Confirm := False;
  end;
end;

function GetCustomSetupExitCode(): Integer;
begin
  if ActivationVerified then Result := 0 else Result := 12;
end;
