import json
import math
import os
from datetime import datetime
from io import BytesIO

import geopandas as gpd
import numpy as np
import rasterio
from fastapi import HTTPException
from PIL import Image
from rasterio.enums import Resampling
from rasterio.windows import from_bounds as window_from_bounds
from rasterio.warp import transform_bounds

import backend.runtime as runtime
from backend.config import RUTA_ORTOMOSAICO_MS, RUTA_ORTOMOSAICO_RGB


def obtener_escala(src, max_pixels):
    total_pixels = max(1, int(src.width) * int(src.height))
    if max_pixels <= 0:
        return 1
    if total_pixels <= max_pixels:
        return 1
    return max(1, math.ceil(math.sqrt(total_pixels / max_pixels)))


def estirar_rgb_color_real(r, g, b, limites=runtime.RGB_STRETCH):
    def _estirar(banda):
        arr = np.asarray(banda, dtype=np.float32)
        if arr.size == 0:
            return np.zeros_like(arr, dtype=np.uint8)

        bajo, alto = np.percentile(arr, limites)
        if not np.isfinite(bajo) or not np.isfinite(alto):
            return np.zeros_like(arr, dtype=np.uint8)
        if alto <= bajo:
            alto = bajo + 1.0

        arr = np.clip(arr, bajo, alto)
        arr = ((arr - bajo) * 255.0) / (alto - bajo)
        return np.clip(arr, 0, 255).astype(np.uint8)

    return _estirar(r), _estirar(g), _estirar(b)


def _rgb_visual_rededge(r, g, b, limites=runtime.RGB_STRETCH, gamma=1.15):
    bandas = [
        np.asarray(r, dtype=np.float32),
        np.asarray(g, dtype=np.float32),
        np.asarray(b, dtype=np.float32),
    ]
    validas = []
    normalizadas = []

    for banda in bandas:
        mascara = np.isfinite(banda) & (banda > 0)
        if not np.any(mascara):
            mascara = np.isfinite(banda)
        validas.append(mascara)

        valores = banda[mascara]
        if valores.size == 0:
            normalizadas.append(np.zeros_like(banda, dtype=np.float32))
            continue

        bajo, alto = np.percentile(valores, limites)
        if not np.isfinite(bajo) or not np.isfinite(alto):
            normalizadas.append(np.zeros_like(banda, dtype=np.float32))
            continue
        if alto <= bajo:
            bajo = float(np.min(valores))
            alto = float(np.max(valores))
        if alto <= bajo:
            normalizadas.append(np.zeros_like(banda, dtype=np.float32))
            continue

        banda_clip = np.clip(banda, bajo, alto)
        normalizadas.append((banda_clip - bajo) / (alto - bajo))

    rgb = np.dstack(normalizadas).astype(np.float32)
    mascara_rgb = np.logical_and.reduce(validas) if validas else np.ones(rgb.shape[:2], dtype=bool)

    if np.any(mascara_rgb):
        medios = np.array(
            [float(np.mean(rgb[..., idx][mascara_rgb])) for idx in range(3)],
            dtype=np.float32,
        )
        objetivo = float(np.mean(medios))
        ganancias = objetivo / np.maximum(medios, 1e-6)
        ganancias = np.clip(ganancias, 0.80, 1.25)

        if medios[1] < min(medios[0], medios[2]) * 0.97:
            ganancias[1] = min(float(ganancias[1]) * 1.08, 1.30)
            ganancias[0] = max(float(ganancias[0]) * 0.96, 0.75)
            ganancias[2] = max(float(ganancias[2]) * 0.96, 0.75)

        rgb *= ganancias.reshape((1, 1, 3))

    rgb = np.clip(rgb, 0.0, 1.0)
    if gamma and gamma != 1.0:
        rgb = np.power(rgb, 1.0 / float(gamma))

    return (rgb * 255.0).astype(np.uint8)


def _ruta_ortomosaico_rgb():
    if os.path.exists(RUTA_ORTOMOSAICO_RGB):
        return RUTA_ORTOMOSAICO_RGB

    proyecto_dir = os.path.join(runtime.BASE_DIR, "proyecto")
    if not os.path.isdir(proyecto_dir):
        return RUTA_ORTOMOSAICO_RGB

    camera_model = runtime.camera_model_actual()
    candidatos = []
    for nombre in os.listdir(proyecto_dir):
        ruta = os.path.join(proyecto_dir, nombre)
        ext = os.path.splitext(nombre.lower())[1]
        if os.path.isfile(ruta) and ext in {".tif", ".tiff"}:
            candidatos.append(ruta)

    candidatos.sort(key=os.path.getmtime, reverse=True)
    if not candidatos:
        return RUTA_ORTOMOSAICO_RGB

    for ruta in candidatos:
        nombre = os.path.basename(ruta).lower()
        if nombre.endswith("_rgb.tif") or nombre.endswith("_rgb.tiff"):
            return ruta

    if camera_model == "rededge_m":
        for ruta in candidatos:
            try:
                with rasterio.open(ruta) as src:
                    if src.count >= 3:
                        return ruta
            except Exception:
                continue
        return None

    for ruta in candidatos:
        try:
            with rasterio.open(ruta) as src:
                if src.count >= 3:
                    return ruta
        except Exception:
            continue

    return candidatos[0]


def _ruta_ortomosaico_ms():
    if os.path.exists(RUTA_ORTOMOSAICO_MS):
        return RUTA_ORTOMOSAICO_MS

    proyecto_dir = os.path.join(runtime.BASE_DIR, "proyecto")
    if not os.path.isdir(proyecto_dir):
        return RUTA_ORTOMOSAICO_MS

    candidatos = []
    for nombre in os.listdir(proyecto_dir):
        ruta = os.path.join(proyecto_dir, nombre)
        ext = os.path.splitext(nombre.lower())[1]
        if os.path.isfile(ruta) and ext in {".tif", ".tiff"}:
            candidatos.append(ruta)

    candidatos.sort(key=os.path.getmtime, reverse=True)
    for ruta in candidatos:
        nombre = os.path.basename(ruta).lower()
        if nombre.endswith("_ms.tif") or nombre.endswith("_ms.tiff"):
            return ruta

    return RUTA_ORTOMOSAICO_MS


def _ortomosaico_rgb_disponible():
    ruta = _ruta_ortomosaico_rgb()
    return bool(ruta) and os.path.exists(ruta)


def _bounds_ortomosaico_rgb():
    ruta = _ruta_ortomosaico_rgb()
    if not ruta or not os.path.exists(ruta):
        return None

    try:
        with rasterio.open(ruta) as src:
            if not src.crs:
                left, bottom, right, top = src.bounds
                if -180.0 <= left <= 180.0 and -180.0 <= right <= 180.0 and -90.0 <= bottom <= 90.0 and -90.0 <= top <= 90.0:
                    return [[bottom, left], [top, right]]
                return None
            left, bottom, right, top = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
            return [[bottom, left], [top, right]]
    except Exception:
        return None


def _tile_vacio_png():
    img = Image.new("RGBA", (runtime.TILE_SIZE, runtime.TILE_SIZE), (0, 0, 0, 0))
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def _generar_png_overlay_rgb():
    ruta = _ruta_ortomosaico_rgb()
    if not ruta or not os.path.exists(ruta):
        with runtime.lock:
            cached_png = runtime.overlay_cache.get("png")
            cached_ruta = runtime.overlay_cache.get("ruta")
        if cached_png is not None and cached_ruta == ruta:
            return cached_png
        raise HTTPException(status_code=404, detail="Ortomosaico RGB no disponible")

    try:
        cache_buster_actual = int(os.path.getmtime(ruta))
    except Exception:
        cache_buster_actual = None

    with runtime.lock:
        cached_png = runtime.overlay_cache.get("png")
        cached_ruta = runtime.overlay_cache.get("ruta")
        cached_buster = runtime.overlay_cache.get("cache_buster")
    if cached_png is not None and cached_ruta == ruta and cached_buster == cache_buster_actual:
        return cached_png

    with rasterio.open(ruta) as src:
        if src.count < 3:
            raise HTTPException(status_code=400, detail="El raster RGB no tiene suficientes bandas")

        total_pixeles = max(1, int(src.width) * int(src.height))
        if total_pixeles > runtime.OVERLAY_RGB_MAX_PIXELS:
            escala = math.sqrt(runtime.OVERLAY_RGB_MAX_PIXELS / float(total_pixeles))
            target_width = max(1, int(src.width * escala))
            target_height = max(1, int(src.height * escala))
        else:
            target_width = src.width
            target_height = src.height

        if runtime.es_rededge_m_actual() and src.count >= 5:
            r = src.read(3, out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            g = src.read(2, out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            b = src.read(1, out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            rgb = _rgb_visual_rededge(r, g, b)
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        else:
            r = src.read(1, out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            g = src.read(2, out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            b = src.read(3, out_shape=(target_height, target_width), resampling=Resampling.bilinear)

            if r.dtype != np.uint8:
                if np.issubdtype(r.dtype, np.integer):
                    scale = 255.0 / float(np.iinfo(r.dtype).max)
                    r = np.clip(r.astype(np.float32) * scale, 0, 255).astype(np.uint8)
                    g = np.clip(g.astype(np.float32) * scale, 0, 255).astype(np.uint8)
                    b = np.clip(b.astype(np.float32) * scale, 0, 255).astype(np.uint8)
                else:
                    r = np.clip(r, 0, 255).astype(np.uint8)
                    g = np.clip(g, 0, 255).astype(np.uint8)
                    b = np.clip(b, 0, 255).astype(np.uint8)

        mask = src.read_masks(1, out_shape=(target_height, target_width), resampling=Resampling.nearest)
        alpha = np.where(mask > 0, 255, 0).astype(np.uint8)
        rgba = np.dstack((r, g, b, alpha))

        buf = BytesIO()
        Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()


def _sincronizar_overlay_proyecto():
    ruta = _ruta_ortomosaico_rgb()
    bounds = _bounds_ortomosaico_rgb()
    png = None
    cache_buster = None
    if ruta and os.path.exists(ruta):
        try:
            png = _generar_png_overlay_rgb()
            cache_buster = int(os.path.getmtime(ruta))
        except Exception:
            png = None
            cache_buster = None
    with runtime.lock:
        if png is not None:
            runtime.overlay_cache["png"] = png
            runtime.overlay_cache["bounds"] = bounds
            runtime.overlay_cache["ruta"] = ruta
            runtime.overlay_cache["cache_buster"] = cache_buster
    runtime.update_ingesta_info(
        overlay_raster=ruta if ruta and os.path.exists(ruta) else None,
        overlay_bounds=bounds,
    )
    return ruta, bounds


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
            if ext in {".geojson", ".json"}:
                return ruta, "geojson"
    return None, None


def _cargar_overlay_shapefile_desde_ruta(ruta):
    gdf = gpd.read_file(ruta)
    if gdf.empty:
        raise HTTPException(status_code=400, detail="El archivo no contiene geometrías válidas")

    if "geometry" not in gdf.columns:
        raise HTTPException(status_code=400, detail="El archivo no contiene una columna geometry")

    gdf = gdf[~gdf.geometry.isna()]
    if hasattr(gdf.geometry, "is_empty"):
        gdf = gdf[~gdf.geometry.is_empty]
    if gdf.empty:
        raise HTTPException(status_code=400, detail="El archivo no contiene geometrías válidas")

    if gdf.crs is not None:
        try:
            gdf = gdf.to_crs(epsg=4326)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"No se pudo reproyectar el archivo a WGS84: {exc}")

    geojson = json.loads(gdf.to_json())
    minx, miny, maxx, maxy = map(float, gdf.total_bounds)
    if not all(np.isfinite([minx, miny, maxx, maxy])):
        raise HTTPException(status_code=400, detail="No se pudieron calcular los bounds del archivo")

    bounds = [[miny, minx], [maxy, maxx]]
    return geojson, bounds


def _sincronizar_overlay_shapefile(geojson=None, bounds=None, nombre=None):
    with runtime.lock:
        if geojson is not None:
            runtime.shapefile_overlay_cache["geojson"] = geojson
            runtime.shapefile_overlay_cache["bounds"] = bounds
            runtime.shapefile_overlay_cache["cache_buster"] = int(datetime.now().timestamp())
            runtime.shapefile_overlay_cache["nombre"] = nombre
        return dict(runtime.shapefile_overlay_cache)


def _leer_tile_ortomosaico(z, x, y):
    ruta = _ruta_ortomosaico_rgb()
    if not ruta or not os.path.exists(ruta):
        raise HTTPException(status_code=404, detail="Ortomosaico RGB no disponible")

    n = 2 ** z
    lon_left = (x / n) * 360.0 - 180.0
    lon_right = ((x + 1) / n) * 360.0 - 180.0
    lat_top = np.degrees(np.arctan(np.sinh(np.pi * (1 - (2 * y) / n))))
    lat_bottom = np.degrees(np.arctan(np.sinh(np.pi * (1 - (2 * (y + 1)) / n))))

    try:
        with rasterio.open(ruta) as src:
            if src.count < 1:
                return _tile_vacio_png()

            left, bottom, right, top = lon_left, lat_bottom, lon_right, lat_top
            if src.crs and src.crs.to_string() != "EPSG:4326":
                left, bottom, right, top = transform_bounds(
                    "EPSG:4326", src.crs.to_string(), left, bottom, right, top, densify_pts=21
                )

            window = window_from_bounds(left, bottom, right, top, transform=src.transform)
            if window.width <= 0 or window.height <= 0:
                return _tile_vacio_png()

            bands = min(3, src.count)
            if bands < 3:
                return _tile_vacio_png()

            if runtime.es_rededge_m_actual() and src.count >= 5:
                r = src.read(
                    3,
                    window=window,
                    out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                    resampling=Resampling.bilinear,
                    boundless=True,
                    fill_value=0,
                )
                g = src.read(
                    2,
                    window=window,
                    out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                    resampling=Resampling.bilinear,
                    boundless=True,
                    fill_value=0,
                )
                b = src.read(
                    1,
                    window=window,
                    out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                    resampling=Resampling.bilinear,
                    boundless=True,
                    fill_value=0,
                )
                rgb = _rgb_visual_rededge(r, g, b)
                r_norm, g_norm, b_norm = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            else:
                r = src.read(
                    1,
                    window=window,
                    out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                    resampling=Resampling.bilinear,
                    boundless=True,
                    fill_value=0,
                )
                g = src.read(
                    2,
                    window=window,
                    out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                    resampling=Resampling.bilinear,
                    boundless=True,
                    fill_value=0,
                )
                b = src.read(
                    3,
                    window=window,
                    out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                    resampling=Resampling.bilinear,
                    boundless=True,
                    fill_value=0,
                )
                r_norm, g_norm, b_norm = estirar_rgb_color_real(r, g, b, limites=runtime.RGB_STRETCH)

            mask = src.read_masks(
                1,
                window=window,
                out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                resampling=Resampling.nearest,
                boundless=True,
            )

            alpha = np.where(mask > 0, 255, 0).astype(np.uint8)
            rgba = np.dstack((r_norm, g_norm, b_norm, alpha))

            img = Image.fromarray(rgba, mode="RGBA")
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.getvalue()
    except Exception:
        return _tile_vacio_png()
