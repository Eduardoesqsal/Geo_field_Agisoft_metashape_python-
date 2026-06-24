import json
import os
import tempfile
import zipfile
from io import BytesIO

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

import backend.runtime as runtime
from backend.services.ingestion import (
    _contar_imagenes_en_directorio,
    _descargar_zip_drive,
    _extraer_zip_a_dataset,
)
from backend.services.overlay import (
    _buscar_archivo_vectorial_extraido,
    _cargar_overlay_shapefile_desde_ruta,
    _generar_png_overlay_rgb,
    _leer_tile_ortomosaico,
    _sincronizar_overlay_shapefile,
    _bounds_ortomosaico_rgb,
    _ruta_ortomosaico_ms,
    _ruta_ortomosaico_rgb,
)
from backend.services.process import detener_proceso, iniciar_proceso

router = APIRouter()


@router.get("/_old_root")
def root():
    return runtime.serve_frontend_index()


@router.get("/")
def root_map():
    return runtime.serve_frontend_index()


@router.get("/status")
def status():
    return runtime.snapshot_state()


@router.get("/logs")
def get_logs():
    return {"logs": runtime.snapshot_logs()}


@router.get("/overlay/status")
def overlay_status():
    ruta = _ruta_ortomosaico_rgb()
    ruta_ms = _ruta_ortomosaico_ms()
    bounds = _bounds_ortomosaico_rgb()
    cache_buster = None
    disponible = bool(ruta) and os.path.exists(ruta)
    disponible_ms = bool(ruta_ms) and os.path.exists(ruta_ms)
    with runtime.lock:
        if not disponible and runtime.overlay_cache.get("png") is not None and runtime.overlay_cache.get("ruta") == ruta:
            disponible = True
            if runtime.overlay_cache.get("bounds") is not None:
                bounds = runtime.overlay_cache["bounds"]
            if runtime.overlay_cache.get("cache_buster") is not None:
                cache_buster = runtime.overlay_cache["cache_buster"]

    if ruta and os.path.exists(ruta):
        try:
            cache_buster = int(os.path.getmtime(ruta))
        except Exception:
            cache_buster = None
    runtime.update_ingesta_info(overlay_raster=ruta if ruta and os.path.exists(ruta) else None, overlay_bounds=bounds)
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
    with runtime.lock:
        disponible = runtime.shapefile_overlay_cache.get("geojson") is not None
        bounds = runtime.shapefile_overlay_cache.get("bounds")
        cache_buster = runtime.shapefile_overlay_cache.get("cache_buster")
        nombre = runtime.shapefile_overlay_cache.get("nombre")
    return {
        "ok": True,
        "disponible": disponible,
        "bounds": bounds,
        "cache_buster": cache_buster,
        "nombre": nombre,
    }


@router.get("/overlay/shapefile.geojson")
def overlay_shapefile_geojson():
    with runtime.lock:
        geojson = runtime.shapefile_overlay_cache.get("geojson")
    if geojson is None:
        raise HTTPException(status_code=404, detail="Overlay shapefile no disponible")
    return Response(content=json.dumps(geojson), media_type="application/geo+json")


@router.post("/overlay/shapefile")
async def overlay_shapefile(file: UploadFile = File(None), archivo: UploadFile = File(None)):
    upload = file or archivo
    if upload is None:
        raise HTTPException(status_code=400, detail="Debes enviar un archivo .zip, .geojson o .json")
    if not upload.filename:
        raise HTTPException(status_code=400, detail="El archivo no tiene nombre")

    nombre = upload.filename.lower()
    ext = os.path.splitext(nombre)[1]
    if ext not in {".zip", ".geojson", ".json"}:
        raise HTTPException(status_code=400, detail="El archivo debe ser .zip, .geojson o .json")

    try:
        contenido = await upload.read()
        if not contenido:
            raise HTTPException(status_code=400, detail="El archivo esta vacio")

        with tempfile.TemporaryDirectory() as tmpdir:
            ruta_vector = None
            if ext == ".zip":
                ruta_zip = os.path.join(tmpdir, "overlay.zip")
                with open(ruta_zip, "wb") as f:
                    f.write(contenido)
                with zipfile.ZipFile(ruta_zip, "r") as zf:
                    zf.extractall(tmpdir)
                ruta_vector, _ = _buscar_archivo_vectorial_extraido(tmpdir)
                if ruta_vector is None:
                    raise HTTPException(
                        status_code=400,
                        detail="El ZIP debe incluir un shapefile (.shp + .dbf + .shx) o un GeoJSON",
                    )
            else:
                ruta_vector = os.path.join(tmpdir, upload.filename)
                with open(ruta_vector, "wb") as f:
                    f.write(contenido)

            geojson, bounds = _cargar_overlay_shapefile_desde_ruta(ruta_vector)
            cache = _sincronizar_overlay_shapefile(geojson=geojson, bounds=bounds, nombre=upload.filename)

        runtime.push_log(f"Overlay vectorial cargado: {upload.filename}")
        return {
            "ok": True,
            "mensaje": "Shapefile cargado y visible como overlay",
            "bounds": cache.get("bounds"),
            "cache_buster": cache.get("cache_buster"),
            "nombre": cache.get("nombre"),
        }
    finally:
        await upload.close()


@router.get("/tiles/rgb/{z}/{x}/{y}.png")
def tile_rgb(z: int, x: int, y: int):
    if z < 0 or z > runtime.MAX_TILE_ZOOM:
        raise HTTPException(status_code=400, detail="Zoom invalido")
    if x < 0 or y < 0 or x >= 2**z or y >= 2**z:
        raise HTTPException(status_code=400, detail="Coordenadas de tile invalidas")
    contenido = _leer_tile_ortomosaico(z, x, y)
    return Response(content=contenido, media_type="image/png")


@router.get("/ingesta/estado")
def estado_ingesta():
    archivos, imagenes_validas = _contar_imagenes_en_directorio()
    info = runtime.snapshot_ingesta_info()
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
        "camera_model": info.get("camera_model") or "mavic_3m",
    }


@router.post("/proyecto/nombre")
def guardar_nombre_proyecto(nombre: str = Form(...)):
    nombre_limpio = runtime.sanitizar_nombre_proyecto(nombre)
    runtime.update_ingesta_info(nombre_proyecto=nombre_limpio)
    runtime.update_state(message=f"Nombre de proyecto guardado: {nombre_limpio}")
    return {"ok": True, "mensaje": "Nombre de proyecto guardado", "nombre_proyecto": nombre_limpio}


@router.post("/proyecto/nuevo")
def nuevo_proyecto(nombre: str = Form(None), camera_model: str = Form(None)):
    with runtime.lock:
        if runtime.state.get("running"):
            raise HTTPException(status_code=409, detail="No puedes reiniciar mientras hay un proceso en ejecucion")

    runtime.limpiar_salidas_proyecto()
    runtime.reset_runtime_state()

    nombre_limpio = runtime.sanitizar_nombre_proyecto(nombre) if nombre else runtime.DEFAULT_INGESTA_INFO["nombre_proyecto"]
    modelo_limpio = runtime.normalizar_modelo_camara(camera_model) if camera_model else runtime.DEFAULT_INGESTA_INFO["camera_model"]

    runtime.update_ingesta_info(nombre_proyecto=nombre_limpio, camera_model=modelo_limpio)
    runtime.update_state(message="Nuevo proyecto listo")
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
    zip_buffer = _descargar_zip_drive(url)
    total_archivos, imagenes_validas = _extraer_zip_a_dataset(zip_buffer)
    return {
        "ok": True,
        "mensaje": "ZIP de Drive descargado y extraido en dataset/",
        "total_archivos": total_archivos,
        "imagenes_validas": imagenes_validas,
    }


@router.get("/procesar")
def procesar():
    return runtime.serve_frontend_index()


@router.post("/procesar")
def procesar_post(nombre_proyecto: str = Form(None), camera_model: str = Form(None)):
    return iniciar_proceso(nombre_proyecto=nombre_proyecto, camera_model=camera_model)


@router.post("/start")
def start(nombre_proyecto: str = Form(None), camera_model: str = Form(None)):
    return iniciar_proceso(nombre_proyecto=nombre_proyecto, camera_model=camera_model)


@router.post("/stop")
def stop():
    return detener_proceso()


@router.get("/{requested_path:path}", include_in_schema=False)
def frontend_fallback(requested_path: str):
    ruta = runtime._frontend_dist_path(requested_path)
    if ruta and os.path.isfile(ruta):
        return FileResponse(ruta)
    return runtime.serve_frontend_index()
