@echo off
REM Gera dist\Djorge Local\Djorge Local.exe e (se o Inno Setup estiver instalado) o instalador.
cd /d %~dp0
if not exist .venv-build (py -3.11 -m venv .venv-build)
call .venv-build\Scripts\activate
pip install -q --index-url https://download.pytorch.org/whl/cpu torch
pip install -q -r requirements.txt winsdk pystray keyring pyinstaller
python -m PyInstaller djorge-local.spec --noconfirm
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" installer.iss
echo Pronto: dist\Djorge Local\Djorge Local.exe  e  Output\DjorgeLocal-Setup-*.exe
pause
