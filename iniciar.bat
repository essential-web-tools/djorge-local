@echo off
cd /d %~dp0
if not exist .venv (py -3.11 -m venv .venv || python -m venv .venv)
call .venv\Scripts\activate
pip install -q -r requirements.txt winsdk
if not exist "%USERPROFILE%\.djorge-local\config.yaml" python -m djorge_local init
python -m djorge_local run
pause
