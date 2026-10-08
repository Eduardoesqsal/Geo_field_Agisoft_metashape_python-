import os
import re
import threading
from copy import deepcopy
from datetime import datetime

from backend.core.naming import DEFAULT_CAMERA_MODEL, DEFAULT_PROJECT_NAME, normalizar_modelo_camara, sanitizar_nombre_proyecto
from backend.core.paths import BASE_DIR, CLIP_GEOJSON, CONTOURS_SCRIPT, FRONTEND_DIST_DIR, FRONTEND_INDEX_FILE, LOGS_DIR, MAIN_SCRIPT, METASHAPE_EXE, PROYECTO_DIR


app_title = "Procesamiento Metashape"
EXTENSIONES_VALIDAS = {".jpg", ".jpeg", ".tif", ".tiff"}
TILE_SIZE = 256
WEB_MERCATOR_CRS = "EPSG:3857"
EARTH_RADIUS = 6378137.0
MAX_TILE_ZOOM = 24
RGB_STRETCH = (2, 98)
OVERLAY_RGB_MAX_PIXELS = 1_500_000

DEFAULT_STATE = {
    "running": False,
    "step": "idle",
    "message": "Listo",
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "error": None,
}
DEFAULT_INGESTA_INFO = {
    "origen": None,
    "actualizado_en": None,
    "total_archivos": 0,
    "imagenes_validas": 0,
    "puntos_gps": [],
    "centro_gps": None,
    "nombre_proyecto": DEFAULT_PROJECT_NAME,
    "camera_model": DEFAULT_CAMERA_MODEL,
    "overlay_raster": None,
    "overlay_bounds": None,
}

state = deepcopy(DEFAULT_STATE)
logs = []
MAX_LOGS = 800
lock = threading.Lock()
process = None
reader_thread = None
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
    "formato": None,
    "superficie_m2": None,
    "superficie_ha": None,
    "feature_count": None,
}
curvas_nivel_cache = {
    "geojson": None,
    "bounds": None,
    "cache_buster": None,
    "intervalo_m": 5,
    "intervalo_maestra_m": 25,
    "feature_count": None,
}


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


def nombre_proyecto_actual():
    info = snapshot_ingesta_info()
    return sanitizar_nombre_proyecto(info.get("nombre_proyecto"))


def camera_model_actual():
    info = snapshot_ingesta_info()
    return normalizar_modelo_camara(info.get("camera_model"))


def es_rededge_m_actual():
    return camera_model_actual() == "rededge_m"


def es_solo_rgb_actual():
    return camera_model_actual() == "mavic_3_rgb"


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
            "formato": None,
            "superficie_m2": None,
            "superficie_ha": None,
            "feature_count": None,
        })
        curvas_nivel_cache.clear()
        curvas_nivel_cache.update({
            "geojson": None,
            "bounds": None,
            "cache_buster": None,
            "intervalo_m": 5,
            "intervalo_maestra_m": 25,
            "feature_count": None,
        })
        global process, reader_thread
        process = None
        reader_thread = None


def limpiar_salidas_proyecto():
    if os.path.exists(CLIP_GEOJSON):
        os.remove(CLIP_GEOJSON)
    if not os.path.isdir(PROYECTO_DIR):
        return

    for nombre in os.listdir(PROYECTO_DIR):
        ruta = os.path.join(PROYECTO_DIR, nombre)
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
            or nombre_lower.endswith("_curvas_5m.geojson")
        ):
            continue
        try:
            os.remove(ruta)
        except Exception:
            pass
