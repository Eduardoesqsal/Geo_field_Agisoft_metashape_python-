import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from fractions import Fraction
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import requests
import gdown
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
    parsed = urlparse(url)
    if parsed.netloc not in {"drive.google.com", "www.drive.google.com"}:
        raise HTTPException(status_code=400, detail="URL invalida: debe ser un enlace publico de Google Drive")

    match = re.search(r"/(?:file/d|folders)/([^/?#]+)", parsed.path)
    if match:
        return match.group(1), "/folders/" in parsed.path

    file_id = parse_qs(parsed.query).get("id", [None])[0]
    if file_id:
        return file_id, False

    raise HTTPException(status_code=400, detail="No se pudo extraer el ID del archivo de Drive")


def _es_ruta_rclone(valor):
    return bool(re.match(r"^[A-Za-z0-9_.-]+:.+", (valor or "").strip()))


def _descargar_carpeta_rclone(ruta_remota):
    """Copia una carpeta de un remote rclone a un directorio temporal."""
    ruta_remota = ruta_remota.strip()
    if not _es_ruta_rclone(ruta_remota):
        raise HTTPException(status_code=400, detail="Ruta rclone invalida. Usa remote:ruta/carpeta")

    try:
        with tempfile.TemporaryDirectory(prefix="rclone_ingesta_") as tmpdir:
            resultado = subprocess.run(
                ["rclone", "copy", ruta_remota, tmpdir, "--create-empty-src-dirs"],
                capture_output=True,
                text=True,
                timeout=3600,
                check=False,
            )
            if resultado.returncode != 0:
                detalle = (resultado.stderr or resultado.stdout or "sin detalle").strip()
                raise HTTPException(status_code=400, detail=f"rclone no pudo descargar la carpeta: {detalle}")

            archivos = [
                os.path.join(root, nombre)
                for root, _, nombres in os.walk(tmpdir)
                for nombre in nombres
            ]
            if not archivos:
                raise HTTPException(status_code=400, detail="La ruta rclone no contiene archivos")

            _limpiar_directorio_imagenes()
            usados = set()
            imagenes_validas = 0
            archivos_zip = []
            for ruta_origen in archivos:
                nombre = os.path.basename(ruta_origen)
                if nombre.lower().endswith(".zip"):
                    archivos_zip.append(ruta_origen)
                    continue
                if not _es_imagen_valida(nombre):
                    continue

                destino_nombre = nombre
                base, extension = os.path.splitext(nombre)
                contador = 2
                while destino_nombre.lower() in usados:
                    destino_nombre = f"{base}_{contador}{extension}"
                    contador += 1
                usados.add(destino_nombre.lower())
                shutil.copy2(ruta_origen, os.path.join(RUTA_IMAGENES, destino_nombre))
                imagenes_validas += 1

            if imagenes_validas == 0 and archivos_zip:
                with open(archivos_zip[0], "rb") as archivo_zip:
                    total, imagenes = _extraer_zip_a_dataset(BytesIO(archivo_zip.read()))
            else:
                total, imagenes = len(archivos), imagenes_validas

        if imagenes == 0:
            raise HTTPException(status_code=400, detail="La ruta rclone no contiene imagenes validas")
        runtime.update_ingesta_info(
            origen="rclone",
            actualizado_en=datetime.now().isoformat(timespec="seconds"),
            total_archivos=total,
            imagenes_validas=imagenes,
        )
        runtime.update_state(step="ingestado", message="Carpeta rclone cargada")
        _actualizar_puntos_gps_desde_dataset()
        return total, imagenes
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=400, detail="rclone tardo demasiado en descargar la carpeta") from exc


def _descargar_zip_drive(url):
    file_id, es_carpeta = _extraer_id_drive(url)
    if es_carpeta:
        raise HTTPException(status_code=500, detail="Las carpetas deben descargarse con _descargar_carpeta_drive")
    session = requests.Session()
    # Drive genera un ZIP temporal cuando se descarga una carpeta desde su UI.
    if es_carpeta:
        download_url = "https://drive.usercontent.google.com/download"
    else:
        download_url = "https://drive.google.com/uc"
    params = {"export": "download", "id": file_id}

    try:
        response = session.get(download_url, params=params, stream=True, timeout=120)
        response.raise_for_status()

        token = None
        for key, value in response.cookies.items():
            if key.startswith("download_warning"):
                token = value
                break

        # En algunas descargas Drive entrega una pagina HTML con un formulario
        # de confirmacion en lugar de usar una cookie.
        if not token and "text/html" in response.headers.get("content-type", "").lower():
            pagina = response.text
            match = re.search(r'name="confirm" value="([^"]+)"', pagina)
            if match:
                token = match.group(1)

        if token:
            response.close()
            params["confirm"] = token
            response = session.get(download_url, params=params, stream=True, timeout=120)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            raise HTTPException(
                status_code=400,
                detail="Drive no permitio la descarga. Verifica que el archivo o carpeta sea publico.",
            )

        contenido = BytesIO()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                contenido.write(chunk)
        contenido.seek(0)
        if not zipfile.is_zipfile(contenido):
            raise HTTPException(
                status_code=400,
                detail="El enlace de Drive no devolvio un ZIP. Comparte una carpeta o archivo ZIP publico.",
            )
        return contenido
    except requests.exceptions.HTTPError as exc:
        raise HTTPException(status_code=400, detail="No se pudo descargar el archivo desde Drive") from exc
    except requests.exceptions.RequestException as exc:
        raise HTTPException(status_code=400, detail="Error de red al descargar desde Drive") from exc


def _descargar_carpeta_drive(url):
    """Descarga una carpeta publica y copia sus imagenes al dataset."""
    file_id, es_carpeta = _extraer_id_drive(url)
    if not es_carpeta:
        raise HTTPException(status_code=400, detail="El enlace no corresponde a una carpeta de Google Drive")

    try:
        with tempfile.TemporaryDirectory(prefix="drive_ingesta_") as tmpdir:
            rutas = gdown.download_folder(
                id=file_id,
                output=tmpdir,
                quiet=False,
                use_cookies=False,
                remaining_ok=True,
            )
            if not rutas:
                runtime.push_log("Drive: gdown no devolvio archivos para la carpeta")
                raise HTTPException(
                    status_code=400,
                    detail="No se pudo descargar la carpeta. Verifica que sea publica y tenga archivos.",
                )

            _limpiar_directorio_imagenes()
            total_archivos = 0
            imagenes_validas = 0
            archivos_zip = []
            usados = set()
            for root, _, files in os.walk(tmpdir):
                for nombre in files:
                    total_archivos += 1
                    ruta_origen = os.path.join(root, nombre)
                    if nombre.lower().endswith(".zip"):
                        archivos_zip.append(ruta_origen)
                        continue
                    if not _es_imagen_valida(nombre):
                        continue

                    destino_nombre = nombre
                    base, extension = os.path.splitext(nombre)
                    contador = 2
                    while destino_nombre.lower() in usados:
                        destino_nombre = f"{base}_{contador}{extension}"
                        contador += 1
                    usados.add(destino_nombre.lower())
                    shutil.copy2(ruta_origen, os.path.join(RUTA_IMAGENES, destino_nombre))
                    imagenes_validas += 1

            # Si la carpeta contiene un ZIP, procesarlo como una carga anidada.
            if imagenes_validas == 0 and archivos_zip:
                with open(archivos_zip[0], "rb") as archivo_zip:
                    total_zip, imagenes_zip = _extraer_zip_a_dataset(BytesIO(archivo_zip.read()))
                runtime.update_ingesta_info(origen="drive", total_archivos=total_zip, imagenes_validas=imagenes_zip)
                return total_zip, imagenes_zip

        if imagenes_validas == 0:
            raise HTTPException(status_code=400, detail="La carpeta no contiene imagenes validas")

        runtime.update_ingesta_info(
            origen="drive",
            actualizado_en=datetime.now().isoformat(timespec="seconds"),
            total_archivos=total_archivos,
            imagenes_validas=imagenes_validas,
        )
        runtime.update_state(step="ingestado", message="Carpeta de Drive cargada")
        _actualizar_puntos_gps_desde_dataset()
        return total_archivos, imagenes_validas
    except HTTPException:
        raise
    except Exception as exc:
        runtime.push_log(f"Drive: error descargando carpeta: {exc}")
        raise HTTPException(
            status_code=400,
            detail=(
                "No se pudo importar la carpeta de Drive. Verifica que sea publica, "
                f"accesible sin iniciar sesion y contenga imagenes o un ZIP: {exc}"
            ),
        ) from exc
