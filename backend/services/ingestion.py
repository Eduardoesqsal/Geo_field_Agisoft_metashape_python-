import os
import shutil
import zipfile
from datetime import datetime
from fractions import Fraction
from io import BytesIO

import requests
from fastapi import HTTPException
from PIL import ExifTags, Image

from backend.config import RUTA_IMAGENES

import backend.runtime as runtime


def _asegurar_directorio_imagenes():
    os.makedirs(RUTA_IMAGENES, exist_ok=True)


def _limpiar_directorio_imagenes():
    _asegurar_directorio_imagenes()
    for nombre in os.listdir(RUTA_IMAGENES):
        ruta = os.path.join(RUTA_IMAGENES, nombre)
        if os.path.isdir(ruta):
            shutil.rmtree(ruta)
        else:
            os.remove(ruta)
    runtime.update_ingesta_info(puntos_gps=[], centro_gps=None, overlay_raster=None, overlay_bounds=None)


def _es_imagen_valida(nombre_archivo):
    _, ext = os.path.splitext(nombre_archivo.lower())
    return ext in runtime.EXTENSIONES_VALIDAS


def _racional_a_float(valor):
    if isinstance(valor, Fraction):
        return valor
    if isinstance(valor, tuple) and len(valor) == 2 and valor[1]:
        return Fraction(valor[0], valor[1])
    try:
        return Fraction(str(valor))
    except Exception:
        return Fraction(float(valor))


def _dms_a_grados(valores):
    grados = _racional_a_float(valores[0])
    minutos = _racional_a_float(valores[1])
    segundos = _racional_a_float(valores[2])
    return float(grados + (minutos / 60) + (segundos / 3600))


def _obtener_gps_de_imagen(ruta_imagen):
    try:
        with Image.open(ruta_imagen) as img:
            exif = img.getexif()
            if not exif:
                return None

            gps_tag = None
            try:
                gps_tag = exif.get_ifd(34853)
            except Exception:
                raw_gps = exif.get(34853)
                if isinstance(raw_gps, dict):
                    gps_tag = raw_gps

            if not gps_tag:
                return None

            gps_tags = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_tag.items()}

            lat = gps_tags.get("GPSLatitude")
            lon = gps_tags.get("GPSLongitude")
            lat_ref = gps_tags.get("GPSLatitudeRef")
            lon_ref = gps_tags.get("GPSLongitudeRef")

            if not lat or not lon or not lat_ref or not lon_ref:
                return None

            latitud = _dms_a_grados(lat)
            longitud = _dms_a_grados(lon)

            if str(lat_ref).upper() == "S":
                latitud = -latitud
            if str(lon_ref).upper() == "W":
                longitud = -longitud

            return latitud, longitud
    except Exception:
        return None


def _actualizar_puntos_gps_desde_dataset():
    puntos = []
    for nombre in sorted(os.listdir(RUTA_IMAGENES)):
        ruta = os.path.join(RUTA_IMAGENES, nombre)
        if not os.path.isfile(ruta) or not _es_imagen_valida(nombre):
            continue
        gps = _obtener_gps_de_imagen(ruta)
        if not gps:
            continue
        latitud, longitud = gps
        puntos.append(
            {
                "nombre": nombre,
                "lat": latitud,
                "lon": longitud,
            }
        )

    centro = None
    if puntos:
        centro = {
            "lat": sum(p["lat"] for p in puntos) / len(puntos),
            "lon": sum(p["lon"] for p in puntos) / len(puntos),
        }

    runtime.update_ingesta_info(puntos_gps=puntos, centro_gps=centro)
    return puntos, centro


def _contar_imagenes_en_directorio():
    _asegurar_directorio_imagenes()
    archivos = []
    imagenes_validas = []
    for root, _, files in os.walk(RUTA_IMAGENES):
        for nombre in files:
            ruta = os.path.join(root, nombre)
            archivos.append(ruta)
            if _es_imagen_valida(nombre):
                imagenes_validas.append(ruta)
    return archivos, imagenes_validas


def _extraer_zip_a_dataset(zip_obj):
    try:
        if not zipfile.is_zipfile(zip_obj):
            raise zipfile.BadZipFile()

        zip_obj.seek(0)
        _limpiar_directorio_imagenes()
        total_archivos = 0
        imagenes_validas = 0

        with zipfile.ZipFile(zip_obj) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                total_archivos += 1
                nombre = os.path.basename(info.filename)
                if not nombre:
                    continue
                if not _es_imagen_valida(nombre):
                    continue

                destino = os.path.join(RUTA_IMAGENES, nombre)
                with zf.open(info) as origen, open(destino, "wb") as salida:
                    shutil.copyfileobj(origen, salida)
                imagenes_validas += 1
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="ZIP corrupto o invalido") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="Permisos insuficientes para escribir en dataset/") from exc

    if total_archivos == 0:
        raise HTTPException(status_code=400, detail="El ZIP no contiene archivos")

    runtime.update_ingesta_info(
        origen="zip",
        actualizado_en=datetime.now().isoformat(timespec="seconds"),
        total_archivos=total_archivos,
        imagenes_validas=imagenes_validas,
    )
    runtime.update_state(step="ingestado", message="Ingesta cargada")
    _actualizar_puntos_gps_desde_dataset()
    return total_archivos, imagenes_validas


def _extraer_id_drive(url):
    if not url or not isinstance(url, str):
        raise HTTPException(status_code=400, detail="URL invalida")

    url = url.strip()
    if "drive.google.com" not in url:
        raise HTTPException(status_code=400, detail="URL invalida: debe ser un enlace publico de Google Drive")

    if "/file/d/" in url:
        parts = url.split("/file/d/", 1)[1]
        return parts.split("/", 1)[0]
    if "id=" in url:
        return url.split("id=", 1)[1].split("&", 1)[0]

    raise HTTPException(status_code=400, detail="No se pudo extraer el ID del archivo de Drive")


def _descargar_zip_drive(url):
    file_id = _extraer_id_drive(url)
    session = requests.Session()
    download_url = "https://drive.google.com/uc?export=download&id={}".format(file_id)

    try:
        response = session.get(download_url, stream=True, timeout=60)
        response.raise_for_status()

        token = None
        for key, value in response.cookies.items():
            if key.startswith("download_warning"):
                token = value
                break

        if token:
            response.close()
            response = session.get(
                download_url,
                params={"confirm": token, "id": file_id},
                stream=True,
                timeout=60,
            )
            response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type and "zip" not in content_type:
            raise HTTPException(status_code=400, detail="El enlace de Drive no parece apuntar a un ZIP publico")

        contenido = BytesIO()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                contenido.write(chunk)
        contenido.seek(0)
        return contenido
    except requests.exceptions.HTTPError as exc:
        raise HTTPException(status_code=400, detail="No se pudo descargar el archivo desde Drive") from exc
    except requests.exceptions.RequestException as exc:
        raise HTTPException(status_code=400, detail="Error de red al descargar desde Drive") from exc
