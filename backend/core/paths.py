import os


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(BASE_DIR, "dataset")
PROYECTO_DIR = os.path.join(BASE_DIR, "proyecto")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
FRONTEND_DIST_DIR = os.path.join(BASE_DIR, "frontend", "dist")
FRONTEND_INDEX_FILE = os.path.join(FRONTEND_DIST_DIR, "index.html")
TILE_SIZE = 256
WEB_MERCATOR_CRS = "EPSG:3857"
EARTH_RADIUS = 6378137.0
MAX_TILE_ZOOM = 24
RGB_STRETCH = (2, 98)
OVERLAY_RGB_MAX_PIXELS = 1_500_000

METASHAPE_EXE = os.environ.get(
    "METASHAPE_EXE",
    r"C:\Program Files\Agisoft\Metashape Pro\metashape.exe",
).strip()
MAIN_SCRIPT = os.path.join(BASE_DIR, "backend", "main.py")
CONTOURS_SCRIPT = os.path.join(BASE_DIR, "backend", "contours_main.py")
