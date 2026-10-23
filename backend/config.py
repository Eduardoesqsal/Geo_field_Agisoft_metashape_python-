import os

from backend.core.naming import sanitizar_nombre_proyecto
from backend.core.paths import BASE_DIR, DATASET_DIR, PROYECTO_DIR

RUTA_IMAGENES = os.environ.get("METASHAPE_DATASET_DIR", DATASET_DIR)
PROYECTO_DIR = os.environ.get("METASHAPE_PROJECT_DIR", PROYECTO_DIR)
CLIP_GEOJSON = os.path.join(PROYECTO_DIR, "recorte_ortomosaico.geojson")


def _nombre_proyecto_desde_entorno():
    nombre = os.environ.get("METASHAPE_PROJECT_NAME", "test_agisoft")
    return sanitizar_nombre_proyecto(nombre)


NOMBRE_PROYECTO = _nombre_proyecto_desde_entorno()
RUTA_PROYECTO = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}.psx")
RUTA_ORTOMOSAICO_RGB = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}_rgb.tif")
RUTA_ORTOMOSAICO_MS = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}_ms.tif")
RUTA_DEM_SIN_CLASIFICAR = os.path.join(PROYECTO_DIR, f"{NOMBRE_PROYECTO}_dem_sin_clasificar.tif")
RUTA_ORTOMOSAICO = RUTA_ORTOMOSAICO_RGB
