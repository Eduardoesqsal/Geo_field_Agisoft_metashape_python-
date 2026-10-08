import json
import os
import tempfile
import zipfile
from datetime import datetime

import geopandas as gpd
import numpy as np
from fastapi import HTTPException
from shapely.geometry import Polygon

import backend.runtime as runtime
from backend.core.paths import CLIP_GEOJSON


def _buscar_archivo_vectorial_extraido(ruta_base):
    for raiz, _, archivos in os.walk(ruta_base):
        for nombre in archivos:
            ext = os.path.splitext(nombre.lower())[1]
            ruta = os.path.join(raiz, nombre)
            if ext == ".shp":
                return ruta, "shp"
    for raiz, _, archivos in os.walk(ruta_base):
        for nombre in archivos:
            ext = os.path.splitext(nombre.lower())[1]
            ruta = os.path.join(raiz, nombre)
            if ext in {".kml", ".geojson", ".json"}:
                return ruta, ext.lstrip(".")
    return None, None


def _extraer_vector_desde_zip(ruta_zip, tmpdir):
    with zipfile.ZipFile(ruta_zip, "r") as zf:
        zf.extractall(tmpdir)
    ruta_vector, tipo = _buscar_archivo_vectorial_extraido(tmpdir)
    if ruta_vector is None:
        raise HTTPException(
            status_code=400,
            detail="El ZIP debe incluir un shapefile (.shp + .dbf + .shx), un KML o un GeoJSON",
        )
    return ruta_vector, tipo


def _filtrar_poligonos(gdf):
    gdf = gdf[~gdf.geometry.isna()]
    if hasattr(gdf.geometry, "is_empty"):
        gdf = gdf[~gdf.geometry.is_empty]
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    return gdf


def _cargar_overlay_vectorial_desde_ruta(ruta):
    gdf = gpd.read_file(ruta)
    return _cargar_overlay_vectorial_desde_gdf(gdf)


def _cargar_overlay_vectorial_desde_gdf(gdf):
    if gdf.empty:
        raise HTTPException(status_code=400, detail="El archivo no contiene geometrías válidas")

    if "geometry" not in gdf.columns:
        raise HTTPException(status_code=400, detail="El archivo no contiene una columna geometry")

    gdf = _filtrar_poligonos(gdf)
    if gdf.empty:
        raise HTTPException(status_code=400, detail="El archivo no contiene geometrías poligonales")

    if gdf.crs is not None:
        try:
            gdf = gdf.to_crs(epsg=4326)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"No se pudo reproyectar el archivo a WGS84: {exc}")
    else:
        try:
            gdf = gdf.set_crs(epsg=4326, allow_override=True)
        except Exception:
            pass

    superficie_m2 = None
    superficie_ha = None
    try:
        gdf_area = gdf.to_crs(epsg=6933)
        superficie_m2 = float(gdf_area.geometry.area.sum())
        superficie_ha = superficie_m2 / 10000.0
    except Exception:
        try:
            gdf_area = gdf.to_crs(epsg=3857)
            superficie_m2 = float(gdf_area.geometry.area.sum())
            superficie_ha = superficie_m2 / 10000.0
        except Exception:
            superficie_m2 = None
            superficie_ha = None

    geojson = json.loads(gdf.to_json())
    minx, miny, maxx, maxy = map(float, gdf.total_bounds)
    if not all(np.isfinite([minx, miny, maxx, maxy])):
        raise HTTPException(status_code=400, detail="No se pudieron calcular los bounds del archivo")

    bounds = [[miny, minx], [maxy, maxx]]
    return geojson, bounds, superficie_m2, superficie_ha, int(len(gdf))


def _sincronizar_overlay_vectorial(
    geojson=None,
    bounds=None,
    nombre=None,
    formato=None,
    superficie_m2=None,
    superficie_ha=None,
    feature_count=None,
):
    with runtime.lock:
        if geojson is not None:
            runtime.shapefile_overlay_cache["geojson"] = geojson
            runtime.shapefile_overlay_cache["bounds"] = bounds
            runtime.shapefile_overlay_cache["cache_buster"] = int(datetime.now().timestamp() * 1000)
            runtime.shapefile_overlay_cache["nombre"] = nombre
            runtime.shapefile_overlay_cache["formato"] = formato
            runtime.shapefile_overlay_cache["superficie_m2"] = superficie_m2
            runtime.shapefile_overlay_cache["superficie_ha"] = superficie_ha
            runtime.shapefile_overlay_cache["feature_count"] = feature_count
        return dict(runtime.shapefile_overlay_cache)


async def cargar_y_sincronizar_desde_upload(upload):
    if upload is None:
        raise HTTPException(status_code=400, detail="Debes enviar un archivo .zip, .kml, .kmz, .geojson o .json")
    if not upload.filename:
        raise HTTPException(status_code=400, detail="El archivo no tiene nombre")

    nombre = upload.filename
    ext = os.path.splitext(nombre.lower())[1]
    if ext not in {".zip", ".kml", ".kmz", ".geojson", ".json", ".shp"}:
        raise HTTPException(status_code=400, detail="El archivo debe ser .zip, .kml, .kmz, .geojson o .json")

    return await _cargar_desde_upload(upload, nombre, ext)


def _guardar_recorte(geojson, nombre, formato):
    os.makedirs(os.path.dirname(CLIP_GEOJSON), exist_ok=True)
    temporal = CLIP_GEOJSON + ".tmp"
    contenido = dict(geojson)
    contenido["_recorte"] = {"nombre": nombre, "formato": formato}
    try:
        with open(temporal, "w", encoding="utf-8") as archivo:
            json.dump(contenido, archivo)
        os.replace(temporal, CLIP_GEOJSON)
    finally:
        if os.path.exists(temporal):
            os.remove(temporal)


def guardar_roi_dibujado(coordenadas):
    if not isinstance(coordenadas, list) or not 3 <= len(coordenadas) <= 2000:
        raise HTTPException(status_code=400, detail="El ROI necesita entre 3 y 2000 vertices")
    try:
        vertices = [(float(p[0]), float(p[1])) for p in coordenadas if isinstance(p, (list, tuple)) and len(p) == 2]
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Las coordenadas del ROI no son validas") from exc
    if len(vertices) != len(coordenadas) or not all(
        np.isfinite(lon) and np.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90
        for lon, lat in vertices
    ):
        raise HTTPException(status_code=400, detail="El ROI debe tener coordenadas WGS84 validas")
    poligono = Polygon(vertices)
    if poligono.is_empty or not poligono.is_valid or poligono.area <= 0:
        raise HTTPException(status_code=400, detail="El ROI se cruza a si mismo o no encierra un area")
    gdf = gpd.GeoDataFrame({"name": ["ROI dibujado"]}, geometry=[poligono], crs="EPSG:4326")
    geojson, bounds, superficie_m2, superficie_ha, feature_count = _cargar_overlay_vectorial_desde_gdf(gdf)
    _guardar_recorte(geojson, "ROI dibujado", "roi")
    cache = _sincronizar_overlay_vectorial(
        geojson=geojson,
        bounds=bounds,
        nombre="ROI dibujado",
        formato="roi",
        superficie_m2=superficie_m2,
        superficie_ha=superficie_ha,
        feature_count=feature_count,
    )
    runtime.push_log("ROI dibujado guardado para recortar los ortomosaicos")
    return {"ok": True, "mensaje": "ROI guardado: recortara los ortomosaicos al procesar", **cache}


def recuperar_recorte_guardado():
    with runtime.lock:
        if runtime.shapefile_overlay_cache.get("geojson") is not None:
            return
    if not os.path.isfile(CLIP_GEOJSON):
        return
    with open(CLIP_GEOJSON, encoding="utf-8") as archivo:
        contenido = json.load(archivo)
    meta = contenido.get("_recorte") or {}
    geojson, bounds, superficie_m2, superficie_ha, feature_count = _cargar_overlay_vectorial_desde_ruta(CLIP_GEOJSON)
    _sincronizar_overlay_vectorial(
        geojson=geojson,
        bounds=bounds,
        nombre=meta.get("nombre") or "Recorte guardado",
        formato=meta.get("formato") or "geojson",
        superficie_m2=superficie_m2,
        superficie_ha=superficie_ha,
        feature_count=feature_count,
    )


async def _cargar_desde_upload(upload, nombre, ext):
    contenido = await upload.read()
    if not contenido:
        raise HTTPException(status_code=400, detail="El archivo esta vacio")

    with tempfile.TemporaryDirectory() as tmpdir:
        ruta_vector = None
        formato = ext.lstrip(".")

        if ext == ".zip":
            ruta_zip = os.path.join(tmpdir, "overlay.zip")
            with open(ruta_zip, "wb") as f:
                f.write(contenido)
            ruta_vector, formato = _extraer_vector_desde_zip(ruta_zip, tmpdir)
        elif ext == ".kmz":
            ruta_kmz = os.path.join(tmpdir, "overlay.kmz")
            with open(ruta_kmz, "wb") as f:
                f.write(contenido)
            ruta_zip = os.path.join(tmpdir, "overlay.zip")
            with open(ruta_zip, "wb") as f:
                f.write(contenido)
            with zipfile.ZipFile(ruta_zip, "r") as zf:
                zf.extractall(tmpdir)
            ruta_vector, formato = _buscar_archivo_vectorial_extraido(tmpdir)
            if ruta_vector is None:
                raise HTTPException(status_code=400, detail="El KMZ debe incluir un archivo KML")
        else:
            ruta_vector = os.path.join(tmpdir, nombre)
            with open(ruta_vector, "wb") as f:
                f.write(contenido)

        geojson, bounds, superficie_m2, superficie_ha, feature_count = _cargar_overlay_vectorial_desde_ruta(ruta_vector)
        # La capa KML importada funciona tambien como limite del ortomosaico.
        # Guardamos GeoJSON WGS84 para el proceso externo de Metashape.
        if formato == "kml":
            _guardar_recorte(geojson, nombre, formato)
        elif os.path.exists(CLIP_GEOJSON):
            os.remove(CLIP_GEOJSON)
        cache = _sincronizar_overlay_vectorial(
            geojson=geojson,
            bounds=bounds,
            nombre=nombre,
            formato=formato,
            superficie_m2=superficie_m2,
            superficie_ha=superficie_ha,
            feature_count=feature_count,
        )

    runtime.push_log(f"Overlay vectorial cargado: {nombre}")
    return {
        "ok": True,
        "mensaje": "KML cargado: recortara los ortomosaicos" if formato == "kml" else "Capa vectorial cargada y visible como overlay",
        "bounds": cache.get("bounds"),
        "cache_buster": cache.get("cache_buster"),
        "nombre": cache.get("nombre"),
        "formato": cache.get("formato"),
        "superficie_m2": cache.get("superficie_m2"),
        "superficie_ha": cache.get("superficie_ha"),
        "feature_count": cache.get("feature_count"),
    }
