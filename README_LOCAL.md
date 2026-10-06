# Uso local de Metashape

Cada computadora ejecuta su propia copia del proyecto y guarda sus archivos en `dataset/`, `proyecto/` y `logs/` de esa misma computadora. El servidor escucha únicamente en `127.0.0.1:8001`. No requiere Redis, Docker, workers, ngrok ni Render.

## Preparación en Windows

1. Instala Python 3.11, Node.js 20 o superior y Agisoft Metashape Pro en la computadora.
2. En la raíz del proyecto, ejecuta `python -m pip install -r requirements.txt`.
3. En `frontend/`, ejecuta `corepack pnpm install` y después `corepack pnpm build`.
4. Vuelve a la raíz del proyecto y ejecuta `python app.py`. Deja esa terminal abierta y entra a `http://127.0.0.1:8001` en esa misma computadora.

El ejecutable de Metashape se busca en `C:\Program Files\Agisoft\Metashape Pro\metashape.exe`. Si está en otra ruta, define `METASHAPE_EXE` antes de iniciar el servidor.

Para desarrollar la interfaz, ejecuta `corepack pnpm dev` dentro de `frontend/` y abre `http://127.0.0.1:5173`. El backend debe estar en marcha en el puerto 8001.

La opción de enlace Drive y la ruta rclone siguen disponibles para importar imágenes; requieren conexión al origen elegido. El procesamiento y los resultados permanecen en la computadora local.
