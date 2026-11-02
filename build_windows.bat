@echo off
REM Gera dist\Djorge\Djorge.exe e, se o Inno Setup 6 estiver instalado, Output\Djorge-Setup-*.exe
REM O instalador NAO traz o modelo: o DOFL e baixado depois pelo card do modelo na interface.
setlocal
cd /d %~dp0
where py >nul 2>nul || (echo Instale o Python 3.11 x64 de python.org & pause & exit /b 1)
if not exist .venv-build (py -3.11 -m venv .venv-build || (echo Falha ao criar o ambiente & pause & exit /b 1))
call .venv-build\Scripts\activate
python -m pip install -q --upgrade pip
REM torch so para CPU (o runtime ONNX ainda importa torch): bem menor que o pacote com CUDA
pip install -q --index-url https://download.pytorch.org/whl/cpu torch || goto :erro
pip install -q -r requirements.txt pyinstaller || goto :erro
if exist build rmdir /s /q build
if exist "dist\Djorge" rmdir /s /q "dist\Djorge"
python -m PyInstaller djorge-local.spec --noconfirm || goto :erro
if not exist "assets\MicrosoftEdgeWebview2Setup.exe" echo AVISO: assets\MicrosoftEdgeWebview2Setup.exe ausente (baixe o bootstrapper do WebView2 da Microsoft)
set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ISCC%" ("%ISCC%" installer.iss || goto :erro) else (echo Inno Setup 6 nao encontrado: gerado so dist\Djorge)
echo Pronto: dist\Djorge\Djorge.exe  e  Output\Djorge-Setup-*.exe
pause
exit /b 0
:erro
echo Falhou. Veja as mensagens acima.
pause
exit /b 1
