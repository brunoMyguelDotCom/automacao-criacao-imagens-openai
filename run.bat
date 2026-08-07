@echo off
REM ============================================================
REM GPT Image Batch Generator — script de execução (Windows)
REM ============================================================

setlocal

cd /d "%~dp0"

REM Verifica Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado. Instale Python 3.12 e adicione ao PATH.
    pause
    exit /b 1
)

REM Cria venv se não existir
if not exist ".venv\Scripts\python.exe" (
    echo Criando ambiente virtual...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo Instalando dependencias...
    pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

REM Executa o pipeline
python main.py %*

endlocal
