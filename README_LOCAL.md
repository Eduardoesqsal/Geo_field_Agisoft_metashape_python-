# Uso local de Metashape

Cada computadora ejecuta su propia copia del proyecto y guarda sus archivos en `dataset/`, `proyecto/` y `logs/` de esa misma computadora. El servidor escucha únicamente en `127.0.0.1:8001`. No requiere Redis, Docker, workers, ngrok ni Render.

## Preparación en Windows

1. Instala Python 3.11, Node.js 20 o superior y Agisoft Metashape Pro en la computadora.
2. En la raíz del proyecto, ejecuta `python -m pip install -r requirements.txt`.
3. En `frontend/`, ejecuta `corepack pnpm install` y después `corepack pnpm build`.
4. Vuelve a la raíz del proyecto y ejecuta `python app.py`. Deja esa terminal abierta y entra a `http://127.0.0.1:8001` en esa misma computadora.

El ejecutable de Metashape se busca en `C:\Program Files\Agisoft\Metashape Pro\metashape.exe`. Si está en otra ruta, define `METASHAPE_EXE` antes de iniciar el servidor.

## Recorte opcional del ortomosaico

Metashape importa el ROI como limite exterior y aplica el recorte durante `exportRaster(clip_to_boundary=True)`; el servidor no reemplaza el TIFF despues de exportarlo.

En **Capa vectorial**, importa un KML con uno o más polígonos o pulsa **Dibujar ROI** y marca al menos tres vértices con clics en el mapa. Después pulsa **Guardar ROI**. Puedes deshacer el último punto o cancelar el dibujo. Al terminar **Procesar**, los TIFF RGB y multiespectral se recortan al área elegida; fuera del polígono queda transparencia y el overlay muestra ese mismo TIFF recortado. **Quitar recorte** desactiva el límite para el siguiente procesamiento. Si no importas KML ni dibujas ROI, los ortomosaicos conservan su extensión completa. Los DEM sin clasificar y de suelo se generan solamente desde las fotos RGB; el ortomosaico multiespectral usa el DEM RGB.

Para desarrollar la interfaz, ejecuta `corepack pnpm dev` dentro de `frontend/` y abre `http://127.0.0.1:5173`. El backend debe estar en marcha en el puerto 8001.

La opción de enlace Drive y la ruta rclone siguen disponibles para importar imágenes; requieren conexión al origen elegido. El procesamiento y los resultados permanecen en la computadora local.
