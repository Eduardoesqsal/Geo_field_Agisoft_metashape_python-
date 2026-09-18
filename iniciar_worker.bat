@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ==========================================
echo  Worker Metashape distribuido
echo ==========================================
echo.

if "%REDIS_HOST%"=="" set REDIS_HOST=192.168.0.38
if "%REDIS_PORT%"=="" set REDIS_PORT=6379

if "%WORKER_ID%"=="" (
    set /p WORKER_ID=ID de este worker ^(PC1, PC2, PC3...; Enter usa nombre de Windows^):
    if "!WORKER_ID!"=="" set WORKER_ID=%COMPUTERNAME%
)

echo.
echo Worker ID: %WORKER_ID%
echo Redis: %REDIS_HOST%:%REDIS_PORT%
echo.

python -c "import redis" >nul 2>&1
if errorlevel 1 (
    echo Instalando dependencias Python...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo No se pudieron instalar las dependencias.
        pause
        exit /b 1
    )
)

echo Iniciando agente. Deja esta ventana abierta.
echo.
python -m backend.workers.agent

echo.
echo Worker detenido.
pause
