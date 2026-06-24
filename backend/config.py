import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_IMAGENES = os.path.join(BASE_DIR, "dataset")


def _nombre_proyecto_desde_entorno():
    nombre = os.environ.get("METASHAPE_PROJECT_NAME", "test_agisoft").strip()
    nombre = re.sub(r"[^A-Za-z0-9._-]+", "_", nombre)
    nombre = nombre.strip("._-")
    return nombre or "test_agisoft"


NOMBRE_PROYECTO = _nombre_proyecto_desde_entorno()
RUTA_PROYECTO = os.path.join(BASE_DIR, "proyecto", f"{NOMBRE_PROYECTO}.psx")
RUTA_ORTOMOSAICO_RGB = os.path.join(BASE_DIR, "proyecto", f"{NOMBRE_PROYECTO}_rgb.tif")
RUTA_ORTOMOSAICO_MS = os.path.join(BASE_DIR, "proyecto", f"{NOMBRE_PROYECTO}_ms.tif")
RUTA_ORTOMOSAICO = RUTA_ORTOMOSAICO_RGB
