; Inno Setup 6 - gera Output\Djorge-Setup-<versão>.exe a partir de dist\Djorge (somente Windows 10/11 x64)
; Pacote enxuto: sem PyTorch (o DOFL roda em ONNX int8) e o modelo (DOFL) NÃO vem no instalador; é baixado depois pelo card do modelo na interface,
; para %LOCALAPPDATA%\Djorge\modelos.
#define AppVer "2.0.0"
[Setup]
AppId={{6E0B7D2A-4C51-4E1B-9C3B-2F0E9D5A7C11}
AppName=Djorge
AppVersion={#AppVer}
AppVerName=Djorge {#AppVer}
AppPublisher=Essential Web Tools
DefaultDirName={localappdata}\Programs\Djorge
DefaultGroupName=Djorge
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
OutputBaseFilename=Djorge-Setup-{#AppVer}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\Djorge.exe
UninstallDisplayName=Djorge
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
; atualização por cima: fecha o Djorge aberto antes de copiar (mesmo mutex do launcher)
AppMutex=Local\DjorgeLocalMutex
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
[Tasks]
Name: "desktop"; Description: "Atalho na área de trabalho"
Name: "autostart"; Description: "Iniciar com o Windows (minimizado na bandeja)"
[InstallDelete]
; versão anterior removida antes de copiar a nova (inclui a pasta de modelos embutida da v1)
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\modelos"
Type: files; Name: "{app}\Djorge Local.exe"
Type: files; Name: "{app}\Djorge Local (diagnostico).exe"
; o atalho de inicialização antigo foi trocado pelo valor HKCU\...\Run (o app liga/desliga sem reinstalar)
Type: files; Name: "{userstartup}\Djorge.lnk"
[Files]
Source: "dist\Djorge\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "assets\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist; Check: NeedsWebView2
[Icons]
Name: "{group}\Djorge"; Filename: "{app}\Djorge.exe"
Name: "{group}\Djorge (diagnóstico)"; Filename: "{app}\Djorge (diagnostico).exe"
Name: "{autodesktop}\Djorge"; Filename: "{app}\Djorge.exe"; Tasks: desktop
[Registry]
; mesmo valor que djorge_local/leveza.py grava (RUN_NAME = "Djorge"): inicia minimizado na bandeja
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Djorge"; ValueData: """{app}\Djorge.exe"" --minimizado"; Flags: uninsdeletevalue; Tasks: autostart
[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Instalando o componente de janela (WebView2)..."; Check: NeedsWebView2 and WebView2SetupExists
Filename: "{app}\Djorge.exe"; Description: "Abrir o Djorge"; Flags: nowait postinstall skipifsilent
[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM ""Djorge.exe"""; Flags: runhidden; RunOnceId: "fechar"
[Code]
function NeedsWebView2(): Boolean;
var v: String;
begin
  Result := not (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0'))
        and not (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0'));
end;

function WebView2SetupExists(): Boolean;
begin
  Result := FileExists(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var d: String;
begin
  { sem a tarefa "Iniciar com o Windows": avisa o app (leveza.py) para não ligar o início sozinho }
  if CurStep = ssPostInstall then
  begin
    d := ExpandConstant('{localappdata}\Djorge');
    ForceDirectories(d);
    if not WizardIsTaskSelected('autostart') then
    begin
      RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Djorge');
      SaveStringToFile(d + '\leveza.inicio-aplicado', 'instalador: desligado', False);
    end
    else
      SaveStringToFile(d + '\leveza.inicio-aplicado', 'instalador: ligado', False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { o modelo baixado e os logs ficam em %LOCALAPPDATA%\Djorge; a pessoa escolhe apagar o modelo ao desinstalar }
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Djorge');
  if CurUninstallStep = usPostUninstall then
  begin
    if (not UninstallSilent()) and DirExists(ExpandConstant('{localappdata}\Djorge\modelos')) then
      if MsgBox('Apagar também o modelo baixado (cerca de 320 MB)?', mbConfirmation, MB_YESNO) = IDYES then
        DelTree(ExpandConstant('{localappdata}\Djorge\modelos'), True, True, True);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var rc: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM "Djorge.exe" /T', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM "Djorge Local.exe" /T', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Sleep(800);
  Result := '';
end;
