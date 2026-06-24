import os
import os
import re
import threading
from datetime import datetime
from copy import deepcopy

from fastapi.responses import FileResponse, HTMLResponse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METASHAPE_EXE = r"C:\Program Files\Agisoft\Metashape Pro\metashape.exe"
MAIN_SCRIPT = os.path.join(BASE_DIR, "backend", "main.py")
EXTENSIONES_VALIDAS = {".jpg", ".jpeg", ".tif", ".tiff"}
TILE_SIZE = 256
WEB_MERCATOR_CRS = "EPSG:3857"
EARTH_RADIUS = 6378137.0
MAX_TILE_ZOOM = 24
RGB_STRETCH = (2, 98)
OVERLAY_RGB_MAX_PIXELS = 1_500_000

app_title = "Procesamiento Metashape"
METASHAPE_EXE = os.environ.get(
    "METASHAPE_EXE",
    r"C:\Program Files\Agisoft\Metashape Pro\metashape.exe",
).strip()
DEFAULT_STATE = {
    "running": False,
    "step": "idle",
    "message": "Listo",
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "error": None,
}
state = deepcopy(DEFAULT_STATE)
logs = []
MAX_LOGS = 800
lock = threading.Lock()
process = None
reader_thread = None
DEFAULT_INGESTA_INFO = {
    "origen": None,
    "actualizado_en": None,
    "total_archivos": 0,
    "imagenes_validas": 0,
    "puntos_gps": [],
    "centro_gps": None,
    "nombre_proyecto": "test_agisoft",
    "camera_model": "mavic_3m",
    "overlay_raster": None,
    "overlay_bounds": None,
}
ingesta_info = deepcopy(DEFAULT_INGESTA_INFO)
overlay_cache = {
    "png": None,
    "bounds": None,
    "ruta": None,
    "cache_buster": None,
}
shapefile_overlay_cache = {
    "geojson": None,
    "bounds": None,
    "cache_buster": None,
    "nombre": None,
}

FRONTEND_DIST_DIR = os.path.join(BASE_DIR, "frontend", "dist")
FRONTEND_INDEX_FILE = os.path.join(FRONTEND_DIST_DIR, "index.html")


def _frontend_dist_path(*parts):
    ruta = os.path.abspath(os.path.join(FRONTEND_DIST_DIR, *parts))
    base = os.path.abspath(FRONTEND_DIST_DIR)
    if not ruta.startswith(base + os.sep) and ruta != base:
        return None
    return ruta


def serve_frontend_index():
    if os.path.exists(FRONTEND_INDEX_FILE):
        return FileResponse(FRONTEND_INDEX_FILE)
    return HTMLResponse(
        "<html><body><h1>Frontend React no compilado</h1><p>Ejecuta el build en <code>frontend/</code>.</p></body></html>",
        status_code=200,
    )


def push_log(line):
    line = line.rstrip()
    if not line:
        return
    stamped = f"{datetime.now().strftime('%H:%M:%S')} | {line}"
    print(stamped, flush=True)
    with lock:
        logs.append(stamped)
        if len(logs) > MAX_LOGS:
            del logs[: len(logs) - MAX_LOGS]


def update_state(**kwargs):
    with lock:
        state.update(kwargs)


def snapshot_state():
    with lock:
        return dict(state)


def snapshot_logs():
    with lock:
        return list(logs)


def update_ingesta_info(**kwargs):
    with lock:
        ingesta_info.update(kwargs)


def snapshot_ingesta_info():
    with lock:
        return dict(ingesta_info)


def sanitizar_nombre_proyecto(nombre):
    nombre = (nombre or "").strip()
    nombre = re.sub(r"[^A-Za-z0-9._-]+", "_", nombre)
    nombre = nombre.strip("._-")
    return nombre or "test_agisoft"


def normalizar_modelo_camara(modelo):
    modelo = (modelo or "mavic_3m").strip().lower().replace(" ", "_")
    alias = {
        "mavic3m": "mavic_3m",
        "mavic_3_multispectral": "mavic_3m",
        "mavic3multispectral": "mavic_3m",
        "mavic_3m": "mavic_3m",
        "rededge": "rededge_m",
        "rededge_m": "rededge_m",
        "rededge-m": "rededge_m",
        "rededgem": "rededge_m",
        "micasense_rededge_m": "rededge_m",
        "micasense_rededge-m": "rededge_m",
    }
    return alias.get(modelo, "mavic_3m")


def nombre_proyecto_actual():
    info = snapshot_ingesta_info()
    return sanitizar_nombre_proyecto(info.get("nombre_proyecto"))


def camera_model_actual():
    info = snapshot_ingesta_info()
    return normalizar_modelo_camara(info.get("camera_model"))


def es_rededge_m_actual():
    return camera_model_actual() == "rededge_m"


def reset_runtime_state():
    with lock:
        state.clear()
        state.update(deepcopy(DEFAULT_STATE))
        logs.clear()
        ingesta_info.clear()
        ingesta_info.update(deepcopy(DEFAULT_INGESTA_INFO))
        overlay_cache.clear()
        overlay_cache.update({
            "png": None,
            "bounds": None,
            "ruta": None,
            "cache_buster": None,
        })
        shapefile_overlay_cache.clear()
        shapefile_overlay_cache.update({
            "geojson": None,
            "bounds": None,
            "cache_buster": None,
            "nombre": None,
        })
        global process, reader_thread
        process = None
        reader_thread = None


def limpiar_salidas_proyecto():
    proyecto_dir = os.path.join(BASE_DIR, "proyecto")
    if not os.path.isdir(proyecto_dir):
        return

    for nombre in os.listdir(proyecto_dir):
        ruta = os.path.join(proyecto_dir, nombre)
        if not os.path.isfile(ruta):
            continue
        nombre_lower = nombre.lower()
        if not (
            nombre_lower.endswith(".psx")
            or nombre_lower.endswith(".tif")
            or nombre_lower.endswith(".tiff")
            or nombre_lower.endswith(".aux.xml")
            or nombre_lower.endswith(".tif.xml")
            or nombre_lower.endswith(".tiff.xml")
        ):
            continue
        try:
            os.remove(ruta)
        except Exception:
            pass
