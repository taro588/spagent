; SP AI Assistant 0.1.0
#define MyAppName "SP AI Assistant"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "taro588"

[Setup]
AppId={{A2A0E5F1-9A8C-4E72-BD7D-7F4D2A1E0C11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={code:GetPainterPluginDir}
DisableDirPage=no
DisableProgramGroupPage=yes
Uninstallable=yes
OutputDir=output
OutputBaseFilename=SP_AI_Assistant_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\plugin\sp_ai_assistant.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\plugin\manifest.json"; DestDir: "{app}"; Flags: ignoreversion

[UninstallDelete]
Type: files; Name: "{app}\sp_ai_assistant.py"
Type: files; Name: "{app}\manifest.json"

[Code]
var
  DetectionText: String;
  PainterDetected: Boolean;

function GetModernPainterRoot(): String;
begin
  Result := ExpandConstant('{userdocs}\Adobe\Adobe Substance 3D Painter');
end;

function GetLegacyPainterRoot(): String;
begin
  Result := ExpandConstant('{userdocs}\Allegorithmic\Substance Painter');
end;

function GetPainterPluginDir(Param: String): String;
var
  Modern, Legacy: String;
begin
  Modern := GetModernPainterRoot() + '\python\plugins';
  Legacy := GetLegacyPainterRoot() + '\python\plugins';

  if DirExists(Modern) then
    Result := Modern
  else if DirExists(Legacy) then
    Result := Legacy
  else
    Result := Modern;
end;

procedure AddPainterCandidate(const ExePath: String);
var
  Major, Minor, Build, Revision: Integer;
  VersionText: String;
begin
  if not FileExists(ExePath) then
    exit;

  PainterDetected := True;
  if GetVersionNumbers(ExePath, Major, Minor, Build, Revision) then
    VersionText := IntToStr(Major) + '.' + IntToStr(Minor) + '.' + IntToStr(Build)
  else
    VersionText := '版本信息不可用';

  DetectionText := DetectionText +
    '✓ Substance 3D Painter ' + VersionText + #13#10 +
    '  程序: ' + ExePath + #13#10;
end;

procedure ScanPainterFolder(const Root: String);
var
  FindRec: TFindRec;
  DirPath, ExePath: String;
begin
  if not DirExists(Root) then
    exit;

  if FindFirst(AddBackslash(Root) + 'Adobe Substance 3D Painter*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          DirPath := AddBackslash(Root) + FindRec.Name;
          ExePath := DirPath + '\Adobe Substance 3D Painter.exe';
          AddPainterCandidate(ExePath);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure DetectPainters();
var
  ProgramFilesRoot, ProgramFilesX86Root: String;
begin
  DetectionText := '';
  PainterDetected := False;

  ProgramFilesRoot := ExpandConstant('{autopf}\Adobe');
  ProgramFilesX86Root := ExpandConstant('{commonpf32}\Adobe');

  ScanPainterFolder(ProgramFilesRoot);
  ScanPainterFolder(ProgramFilesX86Root);

  if FileExists(GetModernPainterRoot() + '\python\plugins\sp_ai_assistant.py') then
    DetectionText := DetectionText +
      #13#10 + '✓ 已发现现有插件安装: ' +
      GetModernPainterRoot() + '\python\plugins' + #13#10;

  if FileExists(GetLegacyPainterRoot() + '\python\plugins\sp_ai_assistant.py') then
    DetectionText := DetectionText +
      #13#10 + '✓ 已发现旧版用户插件目录中的现有安装: ' +
      GetLegacyPainterRoot() + '\python\plugins' + #13#10;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if CurPageID = wpWelcome then
  begin
    DetectPainters();

    if PainterDetected then
      MsgBox(
        '检测到 Substance 3D Painter:' + #13#10 + #13#10 +
        DetectionText + #13#10 +
        '插件将安装到 Painter 官方用户 Python 插件目录。' + #13#10 +
        '不会修改 Painter 核心程序。',
        mbInformation, MB_OK)
    else
      MsgBox(
        '未在常见 Adobe 安装目录中检测到 Substance 3D Painter 可执行文件。' + #13#10 + #13#10 +
        '这不会阻止安装。安装器仍会使用 Adobe 官方用户资源目录:' + #13#10 +
        GetModernPainterRoot() + '\python\plugins' + #13#10 + #13#10 +
        '如果你的 Painter 安装在自定义位置，也可以继续安装；安装后请在 Painter 中重新加载插件。',
        mbInformation, MB_OK);
  end;

  if CurPageID = wpSelectDir then
    MsgBox(
      '当前安装目录:' + #13#10 + ExpandConstant('{app}') + #13#10 + #13#10 +
      '该目录是 Substance 3D Painter 的用户 Python 插件目录。' + #13#10 +
      '不会修改 Painter 核心程序，也不会删除其他插件。',
      mbInformation, MB_OK);
end;

function VerifyInstall(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\sp_ai_assistant.py')) and
           FileExists(ExpandConstant('{app}\manifest.json'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if VerifyInstall() then
      MsgBox(
        'SP AI Assistant 安装成功。' + #13#10 + #13#10 +
        '安装位置:' + #13#10 + ExpandConstant('{app}') + #13#10 + #13#10 +
        '请重新启动 Substance 3D Painter，然后在 Python 菜单中启用插件。',
        mbInformation, MB_OK)
    else
      MsgBox(
        '安装完成但验证失败：未找到插件入口文件。' + #13#10 +
        ExpandConstant('{app}'),
        mbError, MB_OK);
  end;
end;
