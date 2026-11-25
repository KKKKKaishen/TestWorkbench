; Cyu工作台 客户端安装包（Inno Setup）
; 云同步默认预置（双击安装即自动连云）；也可命令行覆盖：/CLOUDURL= /SYNCTOKEN= /SYNCNAME=

#define MyAppName "Cyu工作台"
#define MyAppNameEn "CyuWorkshop"
#define MyAppVersion "2.2.5"

[Setup]
AppId={{8C7F3A52-6B1D-4E8A-9C3F-2E5A7D0B9C11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Cyu Workshop
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\deploy\dist
OutputBaseFilename=CyuWorkshop-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog commandline
UninstallDisplayIcon={app}\CyuWorkshop.exe
; 安装器 exe 自身图标（与客户端一致）
SetupIconFile=E:\Workspace\TestingTable\workbench\pack\cyu.ico
; 1.4GB 载荷，放宽磁盘空间要求自动计算即可

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\CyuWorkshop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\CyuWorkshop.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\CyuWorkshop.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CyuWorkshop.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  CloudUrl, SyncToken, SyncName, Payload: String;
  DataDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    CloudUrl := ExpandConstant('{param:CLOUDURL|https://wb.ka1shen.cn}');
    SyncToken := ExpandConstant('{param:SYNCTOKEN|64dda18ccb108375ed19291450e60ef4282b286c9b9d2c29}');
    SyncName := ExpandConstant('{param:SYNCNAME|Cyu客户端}');
    if (CloudUrl <> '') and (SyncToken <> '') then
    begin
      DataDir := ExpandConstant('{app}\data');
      ForceDirectories(DataDir);
      Payload := '{"cloud_url": "' + CloudUrl + '", "token": "' + SyncToken +
                 '", "client_name": "' + SyncName + '"}';
      SaveStringToFile(DataDir + '\sync_boot.json', Payload, False);
    end;
  end;
end;
