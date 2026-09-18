import json
import math
import os
import re
import tempfile
import threading
import unicodedata
import zipfile
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


_RGB_PROFILE_CACHE = {}
_RGB_PROFILE_CACHE_LOCK = threading.Lock()
_RGB_PROFILE_COMPUTE_LOCK = threading.Lock()
_RGB_PROFILE_CACHE_MAX_ITEMS = 16
RGB_TILE_RENDER_VERSION = "rgb-original-rededge-autodetect-v4"
REDEDGE_COLOR_MATRIX = np.array(
    [
        [1.08, -0.04, -0.04],
        [-0.02, 0.98, 0.04],
        [-0.02, -0.04, 1.06],
    ],
    dtype=np.float32,
)
REDEDGE_DISPLAY_EXPOSURE = 0.78
REDEDGE_DISPLAY_SATURATION = 0.82
REDEDGE_DISPLAY_CONTRAST = 0.94


def _cache_buster_tiles_rgb(ruta):
    if not ruta or not os.path.exists(ruta):
        return None
    try:
        return f"{int(os.path.getmtime(ruta))}-{RGB_TILE_RENDER_VERSION}"
    except OSError:
        return RGB_TILE_RENDER_VERSION


def _raster_tiene_datos_rgb(ruta, *, sample_size=256):
    if not ruta or not os.path.exists(ruta):
        return False

    try:
        with rasterio.open(ruta) as src:
            if src.count < 3 or src.width <= 0 or src.height <= 0:
                return False

            muestras = [
                (0, 0),
                (max(0, src.width // 2 - sample_size // 2), max(0, src.height // 2 - sample_size // 2)),
                (max(0, src.width - sample_size), max(0, src.height - sample_size)),
            ]
            for col_off, row_off in muestras:
                width = min(sample_size, src.width - col_off)
                height = min(sample_size, src.height - row_off)
                if width <= 0 or height <= 0:
                    continue

                ventana = rasterio.windows.Window(col_off, row_off, width, height)
                bandas = src.read([1, 2, 3], window=ventana)
                if np.any(np.isfinite(bandas) & (bandas > 0)):
                    return True
            return False
    except Exception:
        return False


def obtener_escala(src, max_pixels):
    total_pixels = max(1, int(src.width) * int(src.height))
    if max_pixels <= 0:
        return 1
    if total_pixels <= max_pixels:
        return 1
    return max(1, math.ceil(math.sqrt(total_pixels / max_pixels)))


def estirar_rgb_color_real(r, g, b, rangos):
    def _estirar(banda, rango):
        arr = np.asarray(banda, dtype=np.float32)
        if arr.size == 0:
            return np.zeros_like(arr, dtype=np.uint8)

        bajo, alto = rango
        if not np.isfinite(bajo) or not np.isfinite(alto):
            return np.zeros_like(arr, dtype=np.uint8)
        if alto <= bajo:
            alto = bajo + 1.0

        arr = np.clip(arr, bajo, alto)
        arr = ((arr - bajo) * 255.0) / (alto - bajo)
        return np.clip(arr, 0, 255).astype(np.uint8)

    return tuple(_estirar(banda, rango) for banda, rango in zip((r, g, b), rangos))


def _rgb_color_original(r, g, b, dtypes):
    def _convertir(banda, dtype):
        arr = np.asarray(banda)
        tipo = np.dtype(dtype)
        if tipo == np.dtype("uint8"):
            return arr.astype(np.uint8, copy=False)
        if np.issubdtype(tipo, np.integer):
            limites = np.iinfo(tipo)
            escala = 255.0 / float(limites.max - limites.min)
            convertido = (arr.astype(np.float32) - limites.min) * escala
            return np.clip(convertido, 0, 255).astype(np.uint8)

        # Los raster RGB flotantes se interpretan como valores normalizados 0-1.
        convertido = np.asarray(arr, dtype=np.float32) * 255.0
        return np.clip(convertido, 0, 255).astype(np.uint8)

    return tuple(_convertir(banda, dtype) for banda, dtype in zip((r, g, b), dtypes))


def _rgb_visual_rededge(r, g, b, rangos, ganancias):
    normalizadas = estirar_rgb_color_real(r, g, b, rangos)
    rgb = np.dstack(normalizadas).astype(np.float32) / 255.0
    rgb *= np.asarray(ganancias, dtype=np.float32).reshape((1, 1, 3))

    # Revelado visual exclusivo para las bandas visibles de MicaSense. La
    # matriz reduce la dominante verde sin mezclar Red Edge ni NIR.
    rgb = np.einsum("...c,dc->...d", rgb, REDEDGE_COLOR_MATRIX)
    rgb = np.clip(rgb * REDEDGE_DISPLAY_EXPOSURE, 0.0, 1.0)

    # Las bandas son reflectancia lineal; se convierten a una respuesta sRGB
    # antes de suavizar saturacion y contraste para una apariencia fotografica.
    rgb = np.where(
        rgb <= 0.0031308,
        rgb * 12.92,
        1.055 * np.power(rgb, 1.0 / 2.4) - 0.055,
    )
    luminancia = np.sum(
        rgb * np.array([0.2126, 0.7152, 0.0722], dtype=np.float32),
        axis=2,
        keepdims=True,
    )
    rgb = luminancia + (rgb - luminancia) * REDEDGE_DISPLAY_SATURATION
    rgb = (rgb - 0.5) * REDEDGE_DISPLAY_CONTRAST + 0.5

    rgb = np.clip(rgb, 0.0, 1.0)
    return (rgb * 255.0).astype(np.uint8)


def _normalizar_texto_banda(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = texto.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9.]+", " ", texto).strip()


def _es_raster_micasense(src):
    metadatos = []
    for indice in range(1, src.count + 1):
        descripcion = src.descriptions[indice - 1] if indice <= len(src.descriptions) else None
        tags = src.tags(indice)
        metadatos.extend([descripcion or "", *tags.keys(), *tags.values()])

    texto = _normalizar_texto_banda(" ".join(map(str, metadatos)))
    compacto = texto.replace(" ", "")
    tiene_rededge = "rededge" in compacto
    tiene_nir = "nir" in texto.split() or "nearinfrared" in compacto
    return runtime.es_rededge_m_actual() or (src.count >= 5 and tiene_rededge and tiene_nir)


def _canal_desde_texto(texto):
    tokens = set(_normalizar_texto_banda(texto).split())
    if "rededge" in tokens or "red-edge" in tokens or ({"red", "edge"} <= tokens):
        return None
    coincidencias = []
    if tokens & {"red", "rojo"}:
        coincidencias.append("red")
    if tokens & {"green", "verde"}:
        coincidencias.append("green")
    if tokens & {"blue", "azul"}:
        coincidencias.append("blue")
    return coincidencias[0] if len(coincidencias) == 1 else None


def _canal_desde_longitud_onda(tags):
    for clave, valor in tags.items():
        clave_normalizada = _normalizar_texto_banda(clave)
        if not any(token in clave_normalizada for token in ("wavelength", "longitud onda", "center wave")):
            continue
        numeros = re.findall(r"\d+(?:\.\d+)?", str(valor))
        if not numeros:
            continue
        longitud = float(numeros[0])
        if 430 <= longitud < 500:
            return "blue"
        if 500 <= longitud < 600:
            return "green"
        if 600 <= longitud <= 680:
            return "red"
    return None


def _mapear_bandas_rgb(src):
    canales = {}

    for indice, interpretacion in enumerate(src.colorinterp, start=1):
        nombre = _normalizar_texto_banda(getattr(interpretacion, "name", interpretacion))
        if nombre in {"red", "green", "blue"} and nombre not in canales:
            canales[nombre] = indice

    for indice in range(1, src.count + 1):
        if len(canales) == 3:
            break
        tags = src.tags(indice)
        descripcion = src.descriptions[indice - 1] if indice <= len(src.descriptions) else None
        texto = " ".join([str(descripcion or ""), *map(str, tags.keys()), *map(str, tags.values())])
        canal = _canal_desde_texto(texto)
        if canal and canal not in canales and indice not in canales.values():
            canales[canal] = indice

    for indice in range(1, src.count + 1):
        if len(canales) == 3:
            break
        canal = _canal_desde_longitud_onda(src.tags(indice))
        if canal and canal not in canales and indice not in canales.values():
            canales[canal] = indice

    faltantes = [canal for canal in ("red", "green", "blue") if canal not in canales]
    if faltantes:
        detalle = ", ".join(faltantes)
        raise RuntimeError(
            f"El GeoTIFF RedEdge no identifica las bandas RGB en sus metadatos: {detalle}"
        )
    return canales["red"], canales["green"], canales["blue"]


def _clave_cache_perfil(src, indices, rededge, limites):
    ruta = os.path.abspath(src.name) if isinstance(src.name, str) else str(src.name)
    try:
        stat = os.stat(ruta)
        version = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        version = (src.width, src.height, tuple(src.dtypes), tuple(src.nodatavals))
    return ruta, version, tuple(indices), bool(rededge), tuple(limites)


def _crear_perfil_rgb(src, indices, rededge=False, limites=runtime.RGB_STRETCH):
    escala = obtener_escala(src, runtime.OVERLAY_RGB_MAX_PIXELS)
    alto = max(1, math.ceil(src.height / escala))
    ancho = max(1, math.ceil(src.width / escala))
    out_shape = (alto, ancho)

    muestras = []
    mascaras = []
    for indice in indices:
        banda = src.read(indice, out_shape=out_shape, resampling=Resampling.nearest, masked=True)
        datos = np.asarray(banda.data, dtype=np.float32)
        mascara = ~np.ma.getmaskarray(banda)
        nodata = src.nodatavals[indice - 1]
        if nodata is not None and np.isfinite(nodata):
            mascara &= datos != nodata
        mascara &= np.isfinite(datos)
        muestras.append(datos)
        mascaras.append(mascara)

    try:
        mascara_dataset = src.dataset_mask(
            out_shape=out_shape,
            resampling=Resampling.nearest,
        ) > 0
    except Exception:
        mascara_dataset = np.ones(out_shape, dtype=bool)

    mascara_valida = mascara_dataset & np.logical_and.reduce(mascaras)
    mascara_valida &= np.logical_or.reduce([banda != 0 for banda in muestras])
    if not np.any(mascara_valida):
        raise RuntimeError("El GeoTIFF no contiene pixeles RGB validos para calcular el contraste")

    rangos = []
    normalizadas = []
    for banda in muestras:
        valores = banda[mascara_valida]
        bajo, alto = np.percentile(valores, limites)
        if not np.isfinite(bajo) or not np.isfinite(alto):
            raise RuntimeError("No se pudieron calcular los percentiles RGB globales")
        if alto <= bajo:
            bajo = float(np.min(valores))
            alto = float(np.max(valores))
        if alto <= bajo:
            alto = bajo + 1.0
        rangos.append((float(bajo), float(alto)))
        normalizadas.append(np.clip((banda - bajo) / (alto - bajo), 0.0, 1.0))

    ganancias = np.ones(3, dtype=np.float32)
    if rededge:
        medios = np.array(
            [float(np.mean(banda[mascara_valida])) for banda in normalizadas],
            dtype=np.float32,
        )
        objetivo = float(np.mean(medios))
        ganancias = np.clip(objetivo / np.maximum(medios, 1e-6), 0.80, 1.25)
        if medios[1] < min(medios[0], medios[2]) * 0.97:
            ganancias[1] = min(float(ganancias[1]) * 1.08, 1.30)
            ganancias[0] = max(float(ganancias[0]) * 0.96, 0.75)
            ganancias[2] = max(float(ganancias[2]) * 0.96, 0.75)

    perfil = {
        "rangos": tuple(rangos),
        "ganancias": tuple(float(valor) for valor in ganancias),
    }
    return perfil


def _calcular_perfil_rgb(src, indices, rededge=False, limites=runtime.RGB_STRETCH):
    clave = _clave_cache_perfil(src, indices, rededge, limites)
    with _RGB_PROFILE_CACHE_LOCK:
        cached = _RGB_PROFILE_CACHE.get(clave)
    if cached is not None:
        return cached

    with _RGB_PROFILE_COMPUTE_LOCK:
        with _RGB_PROFILE_CACHE_LOCK:
            cached = _RGB_PROFILE_CACHE.get(clave)
        if cached is not None:
            return cached

        perfil = _crear_perfil_rgb(src, indices, rededge=rededge, limites=limites)
        with _RGB_PROFILE_CACHE_LOCK:
            if len(_RGB_PROFILE_CACHE) >= _RGB_PROFILE_CACHE_MAX_ITEMS:
                _RGB_PROFILE_CACHE.clear()
            _RGB_PROFILE_CACHE[clave] = perfil
        return perfil


def _ruta_ortomosaico_rgb():
    if os.path.exists(RUTA_ORTOMOSAICO_RGB):
        return RUTA_ORTOMOSAICO_RGB

    proyecto_dir = os.path.join(runtime.BASE_DIR, "proyecto")
    if not os.path.isdir(proyecto_dir):
        return RUTA_ORTOMOSAICO_RGB

    camera_model = runtime.camera_model_actual()
    if camera_model == "rededge_m":
        if os.path.exists(RUTA_ORTOMOSAICO_MS):
            return RUTA_ORTOMOSAICO_MS

        candidatos_ms = []
        for nombre in os.listdir(proyecto_dir):
            ruta = os.path.join(proyecto_dir, nombre)
            ext = os.path.splitext(nombre.lower())[1]
            if os.path.isfile(ruta) and ext in {".tif", ".tiff"}:
                candidatos_ms.append(ruta)

        candidatos_ms.sort(key=os.path.getmtime, reverse=True)
        for ruta in candidatos_ms:
            nombre = os.path.basename(ruta).lower()
            if nombre.endswith("_ms.tif") or nombre.endswith("_ms.tiff"):
                return ruta

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
    return _raster_tiene_datos_rgb(ruta)


def _bounds_ortomosaico_rgb():
    ruta = _ruta_ortomosaico_rgb()
    if not _raster_tiene_datos_rgb(ruta):
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


def _asegurar_piramides_raster(ruta):
    """Crea overviews internas para que los tiles no lean todo el GeoTIFF."""
    try:
        with rasterio.open(ruta, "r+") as src:
            existentes = src.overviews(1) if src.count else []
            requeridas = [2, 4, 8, 16, 32, 64, 128]
            if all(factor in existentes for factor in requeridas):
                return
            src.build_overviews(requeridas, Resampling.average)
            src.update_tags(ns="rio_overview", resampling="average")
    except Exception as exc:
        runtime.push_log(f"No se pudieron crear piramides del ortomosaico: {exc}")


def _indice_banda_alpha(src):
    for indice, interpretacion in enumerate(src.colorinterp, start=1):
        nombre = _normalizar_texto_banda(getattr(interpretacion, "name", interpretacion))
        if nombre == "alpha":
            return indice

    for indice in range(1, src.count + 1):
        descripcion = src.descriptions[indice - 1] if indice <= len(src.descriptions) else None
        tags = src.tags(indice)
        texto = " ".join([str(descripcion or ""), *map(str, tags.keys()), *map(str, tags.values())])
        tokens = set(_normalizar_texto_banda(texto).split())
        if tokens & {"alpha", "alfa", "transparency", "transparencia"}:
            return indice
    return None


def _leer_alpha_explicito(src, indice, window=None, out_shape=None):
    kwargs = {
        "resampling": Resampling.nearest,
        "masked": True,
    }
    if out_shape is not None:
        kwargs["out_shape"] = out_shape
    if window is not None:
        kwargs.update(window=window, boundless=True, fill_value=0)

    banda = src.read(indice, **kwargs)
    valores = np.asarray(np.ma.filled(banda, 0))
    dtype = np.dtype(src.dtypes[indice - 1])
    if dtype == np.dtype("uint8"):
        return valores.astype(np.uint8, copy=False)
    if np.issubdtype(dtype, np.integer):
        limites = np.iinfo(dtype)
        minimo = max(0, limites.min)
        escala = 255.0 / float(limites.max - minimo)
        alpha = (valores.astype(np.float32) - minimo) * escala
        return np.clip(alpha, 0, 255).astype(np.uint8)

    alpha = np.asarray(valores, dtype=np.float32) * 255.0
    return np.clip(alpha, 0, 255).astype(np.uint8)


def _alpha_desde_raster(src, window=None, out_shape=None, bandas=None):
    def _alpha_desde_bandas():
        if not bandas:
            return np.zeros((out_shape[0], out_shape[1]), dtype=np.uint8) if out_shape else np.zeros((1, 1), dtype=np.uint8)
        try:
            arreglos = [np.asarray(banda) for banda in bandas]
            validos = np.logical_and.reduce([np.isfinite(arr) for arr in arreglos])
            if not np.any(validos):
                return np.zeros_like(arreglos[0], dtype=np.uint8)

            contenido = np.logical_or.reduce([arr > 0 for arr in arreglos])
            alpha_local = np.where(validos & contenido, 255, 0).astype(np.uint8)

            if not _es_raster_micasense(src):
                relleno_blanco = np.logical_and.reduce([arr >= 250 for arr in arreglos])
                alpha_local = np.where(relleno_blanco, 0, alpha_local)

            return alpha_local
        except Exception:
            base = np.asarray(bandas[0])
            return np.where(np.isfinite(base), 255, 0).astype(np.uint8)

    indice_alpha = _indice_banda_alpha(src)
    if indice_alpha is not None:
        try:
            return _leer_alpha_explicito(src, indice_alpha, window=window, out_shape=out_shape)
        except Exception as exc:
            runtime.push_log(f"No se pudo leer la banda Alpha {indice_alpha}: {exc}")

    try:
        mask = src.dataset_mask(
            window=window,
            out_shape=out_shape,
            resampling=Resampling.nearest,
            boundless=True,
        )
    except Exception:
        try:
            mask = src.read_masks(
                1,
                window=window,
                out_shape=out_shape,
                resampling=Resampling.nearest,
                boundless=True,
            )
        except Exception:
            return _alpha_desde_bandas()

    alpha = np.where(mask > 0, 255, 0).astype(np.uint8)
    if not np.any(alpha):
        return _alpha_desde_bandas()

    if _es_raster_micasense(src):
        return alpha

    if bandas:
        try:
            validos = np.logical_and.reduce([np.isfinite(np.asarray(b)) for b in bandas])
            if np.any(validos):
                relleno_negro = np.logical_and.reduce([np.asarray(b) == 0 for b in bandas])
                alpha = np.where(relleno_negro, 0, alpha)

                relleno_blanco = np.logical_and.reduce([np.asarray(b) >= 250 for b in bandas])
                alpha = np.where(relleno_blanco, 0, alpha)
        except Exception:
            pass

    return alpha


def _generar_png_overlay_rgb():
    ruta = _ruta_ortomosaico_rgb()
    if not _raster_tiene_datos_rgb(ruta):
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

        if _es_raster_micasense(src):
            indices_rgb = _mapear_bandas_rgb(src)
            perfil = _calcular_perfil_rgb(src, indices_rgb, rededge=True)
            r_raw = src.read(indices_rgb[0], out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            g_raw = src.read(indices_rgb[1], out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            b_raw = src.read(indices_rgb[2], out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            rgb = _rgb_visual_rededge(
                r_raw,
                g_raw,
                b_raw,
                perfil["rangos"],
                perfil["ganancias"],
            )
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        else:
            indices_rgb = (1, 2, 3)
            r_raw = src.read(1, out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            g_raw = src.read(2, out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            b_raw = src.read(3, out_shape=(target_height, target_width), resampling=Resampling.bilinear)
            dtypes_rgb = tuple(src.dtypes[indice - 1] for indice in indices_rgb)
            r, g, b = _rgb_color_original(r_raw, g_raw, b_raw, dtypes_rgb)

        alpha = _alpha_desde_raster(
            src,
            out_shape=(target_height, target_width),
            bandas=(r_raw, g_raw, b_raw),
        )
        rgba = np.dstack((r, g, b, alpha))

        buf = BytesIO()
        Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()


def _sincronizar_overlay_proyecto():
    ruta = _ruta_ortomosaico_rgb()
    raster_valido = _raster_tiene_datos_rgb(ruta)
    if raster_valido:
        _asegurar_piramides_raster(ruta)
    bounds = _bounds_ortomosaico_rgb()
    png = None
    cache_buster = None
    if raster_valido:
        try:
            png = _generar_png_overlay_rgb()
            cache_buster = int(os.path.getmtime(ruta))
        except Exception as exc:
            runtime.push_log(f"No se pudo preparar el overlay RGB: {exc}")
            png = None
            cache_buster = None
    with runtime.lock:
        if png is not None:
            runtime.overlay_cache["png"] = png
            runtime.overlay_cache["bounds"] = bounds
            runtime.overlay_cache["ruta"] = ruta
            runtime.overlay_cache["cache_buster"] = cache_buster
        else:
            runtime.overlay_cache["png"] = None
            runtime.overlay_cache["bounds"] = None
            runtime.overlay_cache["ruta"] = None
            runtime.overlay_cache["cache_buster"] = None
    runtime.update_ingesta_info(
        overlay_raster=ruta if raster_valido else None,
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
            if ext in {".geojson", ".json", ".kml"}:
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
    if not _raster_tiene_datos_rgb(ruta):
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

            if _es_raster_micasense(src):
                indices_rgb = _mapear_bandas_rgb(src)
                perfil = _calcular_perfil_rgb(src, indices_rgb, rededge=True)
                r = src.read(
                    indices_rgb[0],
                    window=window,
                    out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                    resampling=Resampling.bilinear,
                    boundless=True,
                    fill_value=0,
                )
                g = src.read(
                    indices_rgb[1],
                    window=window,
                    out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                    resampling=Resampling.bilinear,
                    boundless=True,
                    fill_value=0,
                )
                b = src.read(
                    indices_rgb[2],
                    window=window,
                    out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                    resampling=Resampling.bilinear,
                    boundless=True,
                    fill_value=0,
                )
                rgb = _rgb_visual_rededge(
                    r,
                    g,
                    b,
                    perfil["rangos"],
                    perfil["ganancias"],
                )
                r_norm, g_norm, b_norm = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            else:
                indices_rgb = (1, 2, 3)
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
                dtypes_rgb = tuple(src.dtypes[indice - 1] for indice in indices_rgb)
                r_norm, g_norm, b_norm = _rgb_color_original(r, g, b, dtypes_rgb)

            alpha = _alpha_desde_raster(
                src,
                window=window,
                out_shape=(runtime.TILE_SIZE, runtime.TILE_SIZE),
                bandas=(r, g, b),
            )
            rgba = np.dstack((r_norm, g_norm, b_norm, alpha))

            img = Image.fromarray(rgba, mode="RGBA")
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.getvalue()
    except Exception:
        return _tile_vacio_png()
