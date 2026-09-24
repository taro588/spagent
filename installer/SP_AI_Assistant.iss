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

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
    MsgBox('将安装到 Substance 3D Painter 用户 Python 插件目录，不修改 Painter 核心程序。', mbInformation, MB_OK);
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
      MsgBox('SP AI Assistant 安装成功。请重新启动 Substance 3D Painter，然后在 Python 菜单中启用插件。', mbInformation, MB_OK)
    else
      MsgBox('安装完成但验证失败：未找到插件入口文件。', mbError, MB_OK);
  end;
end;
