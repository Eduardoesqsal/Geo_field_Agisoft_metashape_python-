import os

from backend.core.naming import sanitizar_nombre_proyecto
from backend.core.paths import BASE_DIR, DATASET_DIR, PROYECTO_DIR

RUTA_IMAGENES = DATASET_DIR


def _nombre_proyecto_desde_entorno():
    nombre = os.environ.get("METASHAPE_PROJECT_NAME", "test_agisoft")
    return sanitizar_nombre_proyecto(nombre)


NOMBRE_PROYECTO = _nombre_proyecto_desde_entorno()
RUTA_PROYECTO = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}.psx")
RUTA_ORTOMOSAICO_RGB = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}_rgb.tif")
RUTA_ORTOMOSAICO_MS = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}_ms.tif")
RUTA_DEM_SUELO = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}_dem_suelo.tif")
RUTA_DEM_SIN_CLASIFICAR = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}_dem_sin_clasificar.tif")
RUTA_CURVAS_NIVEL = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}_curvas_5m.geojson")
RUTA_ORTOMOSAICO = RUTA_ORTOMOSAICO_RGB
