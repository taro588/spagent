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
DisableDirPage=yes
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
const
  PainterUninstallKey = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall';
  NL = #13#10;

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

function CandidateAlreadyListed(const ExePath: String): Boolean;
begin
  Result := Pos(ExePath, DetectionText) > 0;
end;

procedure AddPainterCandidate(const ExePath: String; const Source: String);
var
  VersionText: String;
begin
  if (ExePath = '') or (not FileExists(ExePath)) then
    exit;

  if CandidateAlreadyListed(ExePath) then
    exit;

  PainterDetected := True;
  if not GetVersionNumbersString(ExePath, VersionText) then
    VersionText := '版本信息不可用';

  DetectionText := DetectionText +
    '✓ Substance 3D Painter ' + VersionText + NL +
    '  来源: ' + Source + NL +
    '  程序: ' + ExePath + NL;
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
          AddPainterCandidate(ExePath, '常见 Adobe 程序目录');
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function CleanDisplayIconPath(const Value: String): String;
var
  S: String;
  P: Integer;
begin
  S := Trim(Value);

  if (Length(S) >= 2) and (S[1] = '"') then
  begin
    P := Pos('"', Copy(S, 2, Length(S) - 1));
    if P > 0 then
      S := Copy(S, 2, P - 1);
  end
  else
  begin
    P := Pos(',', S);
    if P > 0 then
      S := Copy(S, 1, P - 1);
  end;

  Result := Trim(S);
end;

procedure ScanUninstallRegistry(const RootKey: Integer; const RootName: String);
var
  Names: TArrayOfString;
  I: Integer;
  SubKey, DisplayName, InstallLocation, DisplayIcon, ExePath: String;
begin
  if not RegGetSubkeyNames(RootKey, PainterUninstallKey, Names) then
    exit;

  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    SubKey := PainterUninstallKey + '\' + Names[I];

    DisplayName := '';
    if not RegQueryStringValue(RootKey, SubKey, 'DisplayName', DisplayName) then
      continue;

    if Pos('substance 3d painter', LowerCase(DisplayName)) = 0 then
      continue;

    InstallLocation := '';
    ExePath := '';

    if RegQueryStringValue(RootKey, SubKey, 'InstallLocation', InstallLocation) then
      if InstallLocation <> '' then
        ExePath := AddBackslash(InstallLocation) + 'Adobe Substance 3D Painter.exe';

    if (ExePath = '') or (not FileExists(ExePath)) then
    begin
      DisplayIcon := '';
      if RegQueryStringValue(RootKey, SubKey, 'DisplayIcon', DisplayIcon) then
        ExePath := CleanDisplayIconPath(DisplayIcon);
    end;

    AddPainterCandidate(
      ExePath,
      'Windows 卸载注册表 (' + RootName + ')');
  end;
end;

procedure ScanAdobeRegistry();
begin
  { Check both 64-bit and 32-bit registry views, plus per-user uninstall data. }
  ScanUninstallRegistry(HKEY_LOCAL_MACHINE_64, 'HKLM64');
  ScanUninstallRegistry(HKEY_LOCAL_MACHINE_32, 'HKLM32');
  ScanUninstallRegistry(HKEY_CURRENT_USER_64, 'HKCU64');
  ScanUninstallRegistry(HKEY_CURRENT_USER_32, 'HKCU32');
end;

procedure DetectPainters();
var
  ProgramFilesRoot, ProgramFilesX86Root: String;
begin
  DetectionText := '';
  PainterDetected := False;

  { Registry is the primary method because Painter may be installed on D:, E:, etc. }
  ScanAdobeRegistry();

  { Keep common-path scanning as a fallback for portable/custom installations. }
  ProgramFilesRoot := ExpandConstant('{autopf}\Adobe');
  ProgramFilesX86Root := ExpandConstant('{commonpf32}\Adobe');

  ScanPainterFolder(ProgramFilesRoot);
  ScanPainterFolder(ProgramFilesX86Root);

  if FileExists(GetModernPainterRoot() + '\python\plugins\sp_ai_assistant.py') then
    DetectionText := DetectionText +
      NL + '✓ 已发现现有插件安装: ' +
      GetModernPainterRoot() + '\python\plugins' + NL;

  if FileExists(GetLegacyPainterRoot() + '\python\plugins\sp_ai_assistant.py') then
    DetectionText := DetectionText +
      NL + '✓ 已发现旧版用户插件目录中的现有安装: ' +
      GetLegacyPainterRoot() + '\python\plugins' + NL;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if CurPageID = wpWelcome then
  begin
    DetectPainters();

    if PainterDetected then
      MsgBox(
        '检测到 Substance 3D Painter:' + NL + NL +
        DetectionText + NL +
        '插件将自动安装到 Painter 官方用户 Python 插件目录。' + NL +
        '安装目录不会让用户误选到 Painter 程序目录。' + NL +
        '不会修改 Painter 核心程序。',
        mbInformation, MB_OK)
    else
      MsgBox(
        '未检测到 Substance 3D Painter。' + NL + NL +
        '安装器已检查 Windows 卸载注册表、64/32 位注册表视图以及常见 Adobe 安装目录。' + NL +
        '如果你的 Painter 使用非常规安装方式且未写入这些位置，安装器仍会继续安装到官方用户插件目录:' + NL +
        GetModernPainterRoot() + '\python\plugins' + NL + NL +
        '不会修改 Painter 核心程序。',
        mbInformation, MB_OK);
  end;

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
        'SP AI Assistant 安装成功。' + NL + NL +
        '安装位置:' + NL + ExpandConstant('{app}') + NL + NL +
        '请重新启动 Substance 3D Painter，然后在 Python 菜单中启用插件。',
        mbInformation, MB_OK)
    else
      MsgBox(
        '安装完成但验证失败：未找到插件入口文件。' + NL +
        ExpandConstant('{app}'),
        mbError, MB_OK);
  end;
end;
