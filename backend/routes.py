import json
import os
from io import BytesIO

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

import backend.core.state as runtime_state
from backend.core.frontend import frontend_dist_path, serve_frontend_index
from backend.services.ingestion import (
    _descargar_carpeta_drive,
    _descargar_carpeta_rclone,
    _contar_imagenes_en_directorio,
    _descargar_zip_drive,
    _extraer_id_drive,
    _es_ruta_rclone,
    _extraer_zip_a_dataset,
)
from backend.services.overlay import (
    _cache_buster_tiles_rgb,
    _generar_png_overlay_rgb,
    _leer_tile_ortomosaico,
    _bounds_ortomosaico_rgb,
    _raster_tiene_datos_rgb,
    _ruta_ortomosaico_ms,
    _ruta_ortomosaico_rgb,
)
from backend.services.vector_overlay import cargar_y_sincronizar_desde_upload
from backend.services.contours import eliminar_curvas_nivel, estado_curvas_nivel, geojson_curvas_nivel
from backend.services.process import detener_proceso, iniciar_curvas_nivel, iniciar_proceso

router = APIRouter()


@router.get("/_old_root")
def root():
    return serve_frontend_index()


@router.get("/")
def root_map():
    return serve_frontend_index()


@router.get("/status")
def status():
    return runtime_state.snapshot_state()


@router.get("/logs")
def get_logs():
    return {"logs": runtime_state.snapshot_logs()}


@router.get("/overlay/status")
def overlay_status():
    ruta = _ruta_ortomosaico_rgb()
    ruta_ms = _ruta_ortomosaico_ms()
    bounds = _bounds_ortomosaico_rgb()
    cache_buster = None
    disponible = _raster_tiene_datos_rgb(ruta)
    disponible_ms = bool(ruta_ms) and os.path.exists(ruta_ms)
    with runtime_state.lock:
        if not disponible and runtime_state.overlay_cache.get("png") is not None and runtime_state.overlay_cache.get("ruta") == ruta:
            disponible = True
            if runtime_state.overlay_cache.get("bounds") is not None:
                bounds = runtime_state.overlay_cache["bounds"]
            if runtime_state.overlay_cache.get("cache_buster") is not None:
                cache_buster = runtime_state.overlay_cache["cache_buster"]

    if disponible and ruta and os.path.exists(ruta):
        cache_buster = _cache_buster_tiles_rgb(ruta)
    runtime_state.update_ingesta_info(overlay_raster=ruta if disponible else None, overlay_bounds=bounds)
    return {
        "ok": True,
        "disponible": disponible,
        "disponible_ms": disponible_ms,
        "ruta": ruta,
        "ruta_ms": ruta_ms,
        "bounds": bounds,
        "cache_buster": cache_buster,
    }


@router.get("/overlay/rgb.png")
def overlay_rgb_png():
    contenido = _generar_png_overlay_rgb()
    return Response(content=contenido, media_type="image/png")


@router.get("/overlay/contours/status")
def overlay_contours_status():
    return estado_curvas_nivel()


@router.get("/overlay/contours.geojson")
def overlay_contours_geojson():
    geojson = geojson_curvas_nivel()
    if geojson is None:
        raise HTTPException(status_code=404, detail="Curvas de nivel no disponibles")
    return Response(content=json.dumps(geojson), media_type="application/geo+json")


@router.get("/overlay/ms/status")
def overlay_ms_status():
    ruta = _ruta_ortomosaico_ms()
    disponible = bool(ruta) and os.path.exists(ruta)
    return {
        "ok": True,
        "disponible": disponible,
        "ruta": ruta,
    }


@router.get("/overlay/shapefile/status")
def overlay_shapefile_status():
    with runtime_state.lock:
        disponible = runtime_state.shapefile_overlay_cache.get("geojson") is not None
        bounds = runtime_state.shapefile_overlay_cache.get("bounds")
        cache_buster = runtime_state.shapefile_overlay_cache.get("cache_buster")
        nombre = runtime_state.shapefile_overlay_cache.get("nombre")
        formato = runtime_state.shapefile_overlay_cache.get("formato")
        superficie_m2 = runtime_state.shapefile_overlay_cache.get("superficie_m2")
        superficie_ha = runtime_state.shapefile_overlay_cache.get("superficie_ha")
        feature_count = runtime_state.shapefile_overlay_cache.get("feature_count")
    return {
        "ok": True,
        "disponible": disponible,
        "bounds": bounds,
        "cache_buster": cache_buster,
        "nombre": nombre,
        "formato": formato,
        "superficie_m2": superficie_m2,
        "superficie_ha": superficie_ha,
        "feature_count": feature_count,
    }


@router.get("/overlay/vector/status")
def overlay_vector_status():
    return overlay_shapefile_status()


@router.get("/overlay/shapefile.geojson")
def overlay_shapefile_geojson():
    with runtime_state.lock:
        geojson = runtime_state.shapefile_overlay_cache.get("geojson")
    if geojson is None:
        raise HTTPException(status_code=404, detail="Overlay shapefile no disponible")
    return Response(content=json.dumps(geojson), media_type="application/geo+json")


@router.get("/overlay/vector.geojson")
def overlay_vector_geojson():
    return overlay_shapefile_geojson()


@router.post("/overlay/shapefile")
async def overlay_shapefile(file: UploadFile = File(None), archivo: UploadFile = File(None)):
    upload = file or archivo
    if upload is None:
        raise HTTPException(status_code=400, detail="Debes enviar un archivo .zip, .kml, .kmz, .geojson o .json")
    try:
        return await cargar_y_sincronizar_desde_upload(upload)
    finally:
        await upload.close()


@router.post("/overlay/vector")
async def overlay_vector(file: UploadFile = File(None), archivo: UploadFile = File(None)):
    return await overlay_shapefile(file=file, archivo=archivo)


@router.get("/tiles/rgb/{z}/{x}/{y}.png")
def tile_rgb(z: int, x: int, y: int):
    if z < 0 or z > runtime_state.MAX_TILE_ZOOM:
        raise HTTPException(status_code=400, detail="Zoom invalido")
    if x < 0 or y < 0 or x >= 2**z or y >= 2**z:
        raise HTTPException(status_code=400, detail="Coordenadas de tile invalidas")
    contenido = _leer_tile_ortomosaico(z, x, y)
    return Response(content=contenido, media_type="image/png")


@router.get("/ingesta/estado")
def estado_ingesta():
    archivos, imagenes_validas = _contar_imagenes_en_directorio()
    info = runtime_state.snapshot_ingesta_info()
    return {
        "ok": True,
        "mensaje": "Estado de dataset",
        "total_archivos": len(archivos),
        "imagenes_validas": len(imagenes_validas),
        "archivos": [os.path.basename(path) for path in archivos],
        "imagenes_validas_detalle": [os.path.basename(path) for path in imagenes_validas],
        "origen": info.get("origen"),
        "actualizado_en": info.get("actualizado_en"),
        "puntos_gps": info.get("puntos_gps", []),
        "centro_gps": info.get("centro_gps"),
        "nombre_proyecto": info.get("nombre_proyecto") or "test_agisoft",
        "camera_model": info.get("camera_model") or "mavic_3_rgb",
    }


@router.post("/proyecto/nombre")
def guardar_nombre_proyecto(nombre: str = Form(...)):
    nombre_limpio = runtime_state.sanitizar_nombre_proyecto(nombre)
    runtime_state.update_ingesta_info(nombre_proyecto=nombre_limpio)
    runtime_state.update_state(message=f"Nombre de proyecto guardado: {nombre_limpio}")
    return {"ok": True, "mensaje": "Nombre de proyecto guardado", "nombre_proyecto": nombre_limpio}


@router.post("/proyecto/nuevo")
def nuevo_proyecto(nombre: str = Form(None), camera_model: str = Form(None)):
    with runtime_state.lock:
        if runtime_state.state.get("running"):
            raise HTTPException(status_code=409, detail="No puedes reiniciar mientras hay un proceso en ejecucion")

    runtime_state.limpiar_salidas_proyecto()
    runtime_state.reset_runtime_state()

    nombre_limpio = runtime_state.sanitizar_nombre_proyecto(nombre) if nombre else runtime_state.DEFAULT_INGESTA_INFO["nombre_proyecto"]
    modelo_limpio = runtime_state.normalizar_modelo_camara(camera_model) if camera_model else runtime_state.DEFAULT_INGESTA_INFO["camera_model"]

    runtime_state.update_ingesta_info(nombre_proyecto=nombre_limpio, camera_model=modelo_limpio)
    runtime_state.update_state(message="Nuevo proyecto listo")
    return {
        "ok": True,
        "mensaje": "Proyecto reiniciado",
        "nombre_proyecto": nombre_limpio,
        "camera_model": modelo_limpio,
    }


@router.post("/ingesta/zip")
async def ingesta_zip(file: UploadFile = File(None), archivo: UploadFile = File(None)):
    upload = file or archivo
    if upload is None:
        raise HTTPException(status_code=400, detail="Debes enviar un archivo ZIP en el campo file")
    if not upload.filename or not upload.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un .zip")

    try:
        contenido = await upload.read()
        if not contenido:
            raise HTTPException(status_code=400, detail="El ZIP esta vacio")
        total_archivos, imagenes_validas = _extraer_zip_a_dataset(BytesIO(contenido))
        return {
            "ok": True,
            "mensaje": "ZIP procesado y extraido en dataset/",
            "total_archivos": total_archivos,
            "imagenes_validas": imagenes_validas,
        }
    finally:
        await upload.close()


@router.post("/ingesta/drive")
def ingesta_drive(url: str = Form(...)):
    if _es_ruta_rclone(url):
        total_archivos, imagenes_validas = _descargar_carpeta_rclone(url)
    else:
        _file_id, es_carpeta = _extraer_id_drive(url)
        if es_carpeta:
            total_archivos, imagenes_validas = _descargar_carpeta_drive(url)
        else:
            zip_buffer = _descargar_zip_drive(url)
            total_archivos, imagenes_validas = _extraer_zip_a_dataset(zip_buffer)
    return {
        "ok": True,
        "mensaje": "Carpeta o ZIP de Drive descargado y extraido en dataset/",
        "total_archivos": total_archivos,
        "imagenes_validas": imagenes_validas,
    }


@router.get("/procesar")
def procesar():
    return serve_frontend_index()


@router.post("/procesar")
def procesar_post(nombre_proyecto: str = Form(None), camera_model: str = Form(None)):
    return iniciar_proceso(nombre_proyecto=nombre_proyecto, camera_model=camera_model)


@router.post("/start")
def start(nombre_proyecto: str = Form(None), camera_model: str = Form(None)):
    return iniciar_proceso(nombre_proyecto=nombre_proyecto, camera_model=camera_model)


@router.post("/stop")
def stop():
    return detener_proceso()


@router.post("/overlay/contours/generate")
def generate_contours(payload: dict | None = None):
    payload = payload or {}
    return iniciar_curvas_nivel(
        intervalo_m=payload.get("intervalo_m", 5),
        intervalo_maestra_m=payload.get("intervalo_maestra_m", 25),
    )


@router.delete("/overlay/contours")
def delete_contours():
    try:
        return eliminar_curvas_nivel()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/config.js", include_in_schema=False)
def config_js():
    api_base = os.environ.get("API_BASE_URL", "")
    return Response(
        content=f'window.API_BASE = {json.dumps(api_base)};\n',
        media_type="application/javascript",
    )


@router.get("/{requested_path:path}", include_in_schema=False)
def frontend_fallback(requested_path: str):
    ruta = frontend_dist_path(requested_path)
    if ruta and os.path.isfile(ruta):
        return FileResponse(ruta)
    return serve_frontend_index()
