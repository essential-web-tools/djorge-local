; Inno Setup 6 - gera "DjorgeLocal-Setup.exe" a partir de dist\Djorge Local
#define AppVer "0.7.7"
[Setup]
AppId={{6E0B7D2A-4C51-4E1B-9C3B-2F0E9D5A7C11}
AppName=Djorge Local
AppVersion={#AppVer}
AppVerName=Djorge Local {#AppVer}
AppPublisher=Essential Web Tools
DefaultDirName={localappdata}\Programs\Djorge Local
DefaultGroupName=Djorge Local
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
OutputBaseFilename=DjorgeLocal-Setup-{#AppVer}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\Djorge Local.exe
UninstallDisplayName=Djorge Local
; compressão rápida: o instalador abre na hora e instala em segundos (o tamanho cresce um pouco)
Compression=lzma2/fast
SolidCompression=no
PrivilegesRequired=lowest
WizardStyle=modern
; atualização por cima: fecha o Djorge Local aberto antes de copiar
AppMutex=Local\DjorgeLocalMutex
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
[Tasks]
Name: "desktop"; Description: "Atalho na área de trabalho"
Name: "autostart"; Description: "Iniciar com o Windows"; Flags: unchecked
[InstallDelete]
; versão anterior removida antes de copiar a nova (evita arquivos velhos misturados)
Type: filesandordirs; Name: "{app}\_internal"
[Files]
Source: "dist\Djorge Local\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "assets\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2
[Icons]
Name: "{group}\Djorge Local"; Filename: "{app}\Djorge Local.exe"
Name: "{group}\Djorge Local (diagnóstico)"; Filename: "{app}\Djorge Local (diagnostico).exe"
Name: "{autodesktop}\Djorge Local"; Filename: "{app}\Djorge Local.exe"; Tasks: desktop
Name: "{userstartup}\Djorge Local"; Filename: "{app}\Djorge Local.exe"; Tasks: autostart
[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Instalando o componente de janela (WebView2)..."; Check: NeedsWebView2
Filename: "{app}\Djorge Local.exe"; Description: "Abrir o Djorge Local"; Flags: nowait postinstall skipifsilent
[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM ""Djorge Local.exe"""; Flags: runhidden; RunOnceId: "fechar"
[Code]
function NeedsWebView2(): Boolean;
var v: String;
begin
  Result := not (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0'))
        and not (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var rc: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM "Djorge Local.exe" /T', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM "Djorge Local (diagnostico).exe" /T', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Sleep(800);
  Result := '';
end;
