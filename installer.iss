; Inno Setup 6 - gera "DjorgeLocal-Setup.exe" a partir de dist\Djorge Local
#define AppVer "0.7.3"
[Setup]
AppId={{6E0B7D2A-4C51-4E1B-9C3B-2F0E9D5A7C11}
AppName=Djorge Local
AppVersion={#AppVer}
AppPublisher=Essential Web Tools
DefaultDirName={autopf}\Djorge Local
DefaultGroupName=Djorge Local
OutputBaseFilename=DjorgeLocal-Setup-{#AppVer}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\Djorge Local.exe
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
[Tasks]
Name: "desktop"; Description: "Atalho na área de trabalho"
Name: "autostart"; Description: "Iniciar com o Windows"; Flags: unchecked
[Files]
Source: "dist\Djorge Local\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{group}\Djorge Local"; Filename: "{app}\Djorge Local.exe"
Name: "{autodesktop}\Djorge Local"; Filename: "{app}\Djorge Local.exe"; Tasks: desktop
Name: "{userstartup}\Djorge Local"; Filename: "{app}\Djorge Local.exe"; Tasks: autostart
[Run]
Filename: "{app}\Djorge Local.exe"; Description: "Abrir o Djorge Local"; Flags: nowait postinstall skipifsilent
