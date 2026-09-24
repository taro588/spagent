; Inno Setup source
#define MyAppName "SP AI Assistant"
#define MyAppVersion "0.1.0"
[Setup]
AppId={{A2A0E5F1-9A8C-4E72-BD7D-7F4D2A1E0C11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\SP AI Assistant
Uninstallable=yes
OutputDir=output
OutputBaseFilename=SP_AI_Assistant_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
[Files]
Source: "..\plugin\*"; DestDir: "{app}\plugin"; Flags: recursesubdirs createallsubdirs
