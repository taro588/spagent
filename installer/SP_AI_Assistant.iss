; SP AI Assistant Windows installer
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

[Files]
Source: "..\plugin\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
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
  Modern := GetModernPainterRoot() + '\python\plugins\sp_ai_assistant';
  Legacy := GetLegacyPainterRoot() + '\python\plugins\sp_ai_assistant';
  if DirExists(ExtractFileDir(Modern)) then
    Result := Modern
  else if DirExists(ExtractFileDir(Legacy)) then
    Result := Legacy
  else
    Result := Modern;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
    MsgBox('插件将安装到 Substance 3D Painter 用户 Python 插件目录，不修改 Painter 核心程序文件。', mbInformation, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Marker: String;
begin
  if CurStep = ssPostInstall then
  begin
    Marker := ExpandConstant('{app}\manifest.json');
    if FileExists(Marker) then
      MsgBox('SP AI Assistant 安装成功。请启动 Substance 3D Painter，在 Python 菜单中启用插件。', mbInformation, MB_OK)
    else
      MsgBox('安装完成但验证失败：未找到 manifest.json。', mbError, MB_OK);
  end;
end;
