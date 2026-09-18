import os

from fastapi.responses import FileResponse, HTMLResponse

from backend.core.paths import FRONTEND_DIST_DIR, FRONTEND_INDEX_FILE


def frontend_dist_path(*parts):
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
