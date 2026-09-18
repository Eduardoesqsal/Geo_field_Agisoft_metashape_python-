@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo  PC Central - Sistema distribuido local
echo ==========================================
echo.

if not exist logs mkdir logs

for /f %%i in ('docker ps -a --filter "name=^/redis-metashape$" --format "{{.Names}}"') do set REDIS_CONTAINER=%%i
if "%REDIS_CONTAINER%"=="" (
    echo Creando Redis...
    docker run -d --name redis-metashape -p 6379:6379 redis:7
) else (
    echo Redis ya existe. Iniciandolo si esta detenido...
    docker start redis-metashape >nul
)

echo.
echo Verificando Redis...
docker exec redis-metashape redis-cli ping
if errorlevel 1 (
    echo Redis no respondio correctamente.
    pause
    exit /b 1
)

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

set PORT=8001
echo.
echo Central lista.
echo API local: http://localhost:%PORT%
echo API para workers: http://192.168.0.38:%PORT%
echo Redis para workers: 192.168.0.38:6379
echo.

curl -s http://localhost:%PORT%/api/distributed/redis/ping | findstr /c:"\"ok\":true" >nul
if not errorlevel 1 (
    echo FastAPI ya esta corriendo en el puerto %PORT% con las rutas distribuidas.
    echo Puedes abrir http://127.0.0.1:%PORT% y usar el boton Workers.
    echo.
    pause
    exit /b 0
)

netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo El puerto %PORT% esta ocupado por otro proceso.
    echo Cierra la otra ventana del backend o deten ese proceso y vuelve a intentar.
    echo.
    pause
    exit /b 1
)

echo Iniciando FastAPI. Deja esta ventana abierta.
echo.

python app.py

echo.
echo Central detenida.
pause
