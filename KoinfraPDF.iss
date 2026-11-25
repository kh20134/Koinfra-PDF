; Koinfra PDF - Inno Setup 6 script
; Build: pyinstaller --distpath dist_installer --workpath build_installer KoinfraPDFEdit_installer.spec
;        "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" KoinfraPDF.iss   ->  installer\KoinfraPDF_Setup_1.3.2.exe
; Windows 10/11 protect the PDF default (UserChoice + hash): this installer never writes it. It registers
; Koinfra PDF as a PDF handler and, on the finish page, starts the Windows default-app flow where the user
; clicks '항상' / '기본값 설정' once.

#define AppName "Koinfra PDF"
#define AppVersion "1.3.2"
#define AppPublisher "Korea Infra Management"
#define AppExeName "KoinfraPDFEdit.exe"
#define ProgId "Koinfra.PDFEdit.Document"
#define RegName "KoinfraPDFEdit"
#define AumId "Koinfra.PDFEdit.KoinfraPDFEdit"
#define CapKey "Software\Koinfra\KoinfraPDFEdit\Capabilities"
#define DocIcon "koinfra_pdf_doc.ico"

[Setup]
AppId={{6E0B5D4C-6F1E-4C0B-9B6B-3F7A1E2C9A51}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion=1.3.2.0
VersionInfoDescription={#AppName} 설치
DefaultDirName={autopf}\Koinfra PDF
DefaultGroupName=Koinfra PDF
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
SetupIconFile=icon2.ico
WizardSmallImageFile=wizard_small_55.bmp,wizard_small_110.bmp
OutputDir=installer
OutputBaseFilename=KoinfraPDF_Setup_{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; All users (Program Files, HKLM) by default; "only for me" / /CURRENTUSER -> per-user folder + HKCU.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline dialog
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist_installer\KoinfraPDFEdit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "icon2.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DocIcon}"; DestDir: "{app}"; Flags: ignoreversion
Source: "ICONS_LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Koinfra PDF"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icon2.ico"; AppUserModelID: "{#AumId}"
Name: "{autoprograms}\Koinfra PDF 제거"; Filename: "{uninstallexe}"; IconFilename: "{app}\icon2.ico"
Name: "{autodesktop}\Koinfra PDF"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icon2.ico"; AppUserModelID: "{#AumId}"; Tasks: desktopicon

[Registry]
; ProgID (PDF files opened by Koinfra show the blue Koinfra PDF document icon)
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "Koinfra PDF 문서"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "Koinfra PDF 문서"
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: "AppUserModelID"; ValueData: "{#AumId}"
Root: HKA; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#DocIcon}"
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""
; .pdf -> Open with list
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithList\{#AppExeName}"; Flags: uninsdeletekey
; Applications\<exe>
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\shell\open"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""
; App Paths (Win+R "KoinfraPDFEdit")
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#AppExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName}"; Flags: uninsdeletekey
; a stale "Path" from an old portable build would point Windows at a folder that no longer exists
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#AppExeName}"; ValueType: none; ValueName: "Path"; Flags: deletevalue
; Capabilities + RegisteredApplications (Settings > Default apps)
Root: HKA; Subkey: "Software\Koinfra\KoinfraPDFEdit"; Flags: uninsdeletekey
Root: HKA; Subkey: "{#CapKey}"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"
Root: HKA; Subkey: "{#CapKey}"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Koinfra PDF 편집기 (병합·도장·결재)"
Root: HKA; Subkey: "{#CapKey}"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: "{app}\{#AppExeName},0"
Root: HKA; Subkey: "{#CapKey}\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "{#ProgId}"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#RegName}"; ValueData: "{#CapKey}"; Flags: uninsdeletevalue
; Keys the app itself keeps current under HKCU: removed on uninstall (dontcreatekey = only delete).
Root: HKCU; Subkey: "Software\Classes\{#ProgId}"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExeName}"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithList\{#AppExeName}"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: none; ValueName: "{#ProgId}"; Flags: uninsdeletevalue dontcreatekey
Root: HKCU; Subkey: "Software\Koinfra\KoinfraPDFEdit"; Flags: uninsdeletekey dontcreatekey
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: none; ValueName: "{#RegName}"; Flags: uninsdeletevalue dontcreatekey

[Run]
; Checked by default: opens Koinfra PDF and immediately starts the Windows default-app flow
; (app picker -> '항상', or Settings > Default apps -> '기본값 설정'); shows '✔ 설정 완료' when Windows confirms.
Filename: "{app}\{#AppExeName}"; Parameters: "--set-default-pdf"; Description: "PDF 기본 앱으로 설정 (Koinfra PDF 실행)"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  // refresh the shell's icon cache so 'Open with' / Default apps show the Koinfra icon right away (explorer keeps running)
  if CurStep = ssPostInstall then
    Exec(ExpandConstant('{sys}\ie4uinit.exe'), '-show', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteKeyIfEmpty(HKCU, 'Software\Koinfra');
end;
