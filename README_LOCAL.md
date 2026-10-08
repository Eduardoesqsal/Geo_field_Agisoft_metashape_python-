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

En **Capa vectorial**, importa un KML con uno o más polígonos o pulsa **Dibujar ROI** y marca al menos tres vértices con clics en el mapa. Después pulsa **Guardar ROI**. Puedes deshacer el último punto o cancelar el dibujo. Al terminar **Procesar**, los TIFF RGB y multiespectral se recortan al área elegida; fuera del polígono queda transparencia y el overlay muestra ese mismo TIFF recortado. **Quitar recorte** desactiva el límite para el siguiente procesamiento. Si no importas KML ni dibujas ROI, los ortomosaicos conservan su extensión completa. Con Mavic 3M, RGB y MS generan su propio DEM de suelo; cada ortomosaico se construye con el DEM de su mismo grupo de fotos.

Para Mavic 3M, el archivo unico `proyecto/<nombre>_ms.tif` contiene cuatro bandas espectrales de reflectancia Float32: Green (1), Red (2), Red edge (3) y NIR (4), mas una banda Alpha (5) para transparencia. El GeoTIFF usa WGS84/UTM, NoData `-10000`, bloques LZW de 256 pixeles y piramides internas, siguiendo la estructura del archivo de referencia de PIX4Dfields. En PIX4Dfields, importalo mediante **Importar > GeoTIFF**. NDVI usa las bandas 4 y 2; NDRE usa las bandas 4 y 3.

En QGIS, abre el mismo archivo como capa raster. Para verlo en falso color, usa renderizado multibanda con rojo=NIR (4), verde=Red (2) y azul=Green (1); las cuatro bandas siguen disponibles para analisis. Cierra la capa en QGIS antes de volver a procesar el mismo nombre de archivo, ya que Windows puede impedir reemplazar un TIFF abierto.

Para desarrollar la interfaz, ejecuta `corepack pnpm dev` dentro de `frontend/` y abre `http://127.0.0.1:5173`. El backend debe estar en marcha en el puerto 8001.

La opción de enlace Drive y la ruta rclone siguen disponibles para importar imágenes; requieren conexión al origen elegido. El procesamiento y los resultados permanecen en la computadora local.
