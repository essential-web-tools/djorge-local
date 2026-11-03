@echo off
REM Gera dist\Djorge\Djorge.exe e, se o Inno Setup 6 estiver instalado, Output\Djorge-Setup-*.exe
REM O instalador NAO traz o modelo: o DOFL e baixado depois pelo card do modelo na interface.
setlocal
cd /d %~dp0
where py >nul 2>nul || (echo Instale o Python 3.11 x64 de python.org & pause & exit /b 1)
if not exist .venv-build (py -3.11 -m venv .venv-build || (echo Falha ao criar o ambiente & pause & exit /b 1))
call .venv-build\Scripts\activate
python -m pip install -q --upgrade pip
REM SEM torch: o modelo roda em ONNX Runtime (int8) com as cabecas em numpy. Se houver torch neste ambiente,
REM o .spec o exclui do pacote mesmo assim (o instalador fica centenas de MB menor).
pip install -q -r requirements.txt pyinstaller || goto :erro
python -c "import sys; sys.modules['torch']=None; import djorge, djorge.runtime.semantic, djorge.runtime.onnx_backend, djorge_local.models, djorge_local.leveza" || (echo O caminho ONNX importou torch & goto :erro)
if exist build rmdir /s /q build
if exist "dist\Djorge" rmdir /s /q "dist\Djorge"
python -m PyInstaller djorge-local.spec --noconfirm || goto :erro
if not exist "assets\MicrosoftEdgeWebview2Setup.exe" echo AVISO: assets\MicrosoftEdgeWebview2Setup.exe ausente (baixe o bootstrapper do WebView2 da Microsoft)
set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ISCC%" ("%ISCC%" installer.iss || goto :erro) else (echo Inno Setup 6 nao encontrado: gerado so dist\Djorge)
echo Pronto: dist\Djorge\Djorge.exe  e  Output\Djorge-Setup-*.exe
if not defined CI pause
exit /b 0
:erro
echo Falhou. Veja as mensagens acima.
if not defined CI pause
exit /b 1
