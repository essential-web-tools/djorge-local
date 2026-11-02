@echo off
REM Desenvolvimento no Windows: cria o ambiente, instala as dependências e abre a janela do Djorge.
cd /d %~dp0
if not exist .venv (py -3.11 -m venv .venv || python -m venv .venv)
call .venv\Scripts\activate
pip install -q --index-url https://download.pytorch.org/whl/cpu torch
pip install -q -r requirements.txt
python run_djorge_local.py
if errorlevel 1 pause
