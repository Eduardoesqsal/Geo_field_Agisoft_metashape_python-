import json
import os
import re
import time
import uuid
from collections import defaultdict

import numpy as np
from PIL import Image

try:
    import rasterio
    from rasterio.enums import ColorInterp, Resampling
except Exception:
    rasterio = None

from backend.config import (
    CLIP_GEOJSON,
    RUTA_DEM_SIN_CLASIFICAR,
    RUTA_IMAGENES,
    RUTA_ORTOMOSAICO_MS,
    RUTA_ORTOMOSAICO_RGB,
    RUTA_PROYECTO,
)
from backend.core.paths import DATASET_DIR
from backend.services.clip import recortar_ortomosaico


class ProcesamientoMetashape:
    REDEDGE_RGB_STRETCH = (2, 98)
    REDEDGE_RGB_GAMMA = 1.15
    ALIGNMENT_DOWNSCALE = 4
    DEM_RESOLUTION_METERS = 0.5
    ORTHOMOSAIC_RESOLUTION_METERS = 0.2

    def __init__(self, progress_callback=None, camera_model=None):
        self.doc = None
        self.progress_callback = progress_callback
        self._metashape = None
        self.camera_model = self._normalizar_modelo_camara(camera_model)
        self._tempdirs = []

    def _normalizar_modelo_camara(self, camera_model):
        modelo = (camera_model or "mavic_3_rgb").strip().lower().replace(" ", "_")
        alias = {
            "mavic3rgb": "mavic_3_rgb",
            "mavic_3_rgb": "mavic_3_rgb",
            "mavic_3_pro": "mavic_3_rgb",
            "mavic_3": "mavic_3_rgb",
            "rgb": "mavic_3_rgb",
            "mavic3m": "mavic_3m",
            "mavic_3_multispectral": "mavic_3m",
            "mavic_3m": "mavic_3m",
            "mavic3multispectral": "mavic_3m",
            "rededge": "rededge_m",
            "rededge_m": "rededge_m",
            "rededge-m": "rededge_m",
            "rededgem": "rededge_m",
            "micasense_rededge_m": "rededge_m",
            "micasense_rededge-m": "rededge_m",
        }
        return alias.get(modelo, "mavic_3_rgb")

    def _es_solo_rgb(self):
        return self.camera_model == "mavic_3_rgb"

    def _ms(self):
        if self._metashape is None:
            try:
                import Metashape as metashape
            except ModuleNotFoundError as exc:
                raise ModuleNotFoundError("Usa el Python de Agisoft Metashape.") from exc
            self._metashape = metashape
            geoid_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "geoids", "mx_inegi_GGM10.tif")
            if os.path.exists(geoid_path) and hasattr(metashape, "addGeoid"):
                try:
                    metashape.addGeoid(geoid_path)
                except Exception:
                    pass
        return self._metashape

    def _enum(self, *paths):
        ms = self._ms()
        for path in paths:
            obj = ms
            ok = True
            for part in path.split("."):
                if not hasattr(obj, part):
                    ok = False
                    break
                obj = getattr(obj, part)
            if ok:
                return obj
        return None

    def _log(self, msg):
        print(msg, flush=True)
        if self.progress_callback:
            self.progress_callback(msg)

    def _abrir_documento(self, crear=False):
        if self.doc:
            return self.doc
        Metashape = self._ms()
        doc = Metashape.Document()
        if crear:
            self.doc = doc
            return doc
        if os.path.exists(RUTA_PROYECTO):
            doc.open(RUTA_PROYECTO)
        self.doc = doc
        return doc

    def _guardar_documento(self, ruta=None):
        doc = self._abrir_documento(crear=ruta is not None)
        doc.save(ruta) if ruta else doc.save()

    def _obtener_chunks(self):
        return self._abrir_documento().chunks

    def limpiar_temporales(self):
        while self._tempdirs:
            tempdir = self._tempdirs.pop()
            try:
                tempdir.cleanup()
            except Exception:
                pass

    def _es_imagen(self, nombre):
        return os.path.splitext(nombre.lower())[1] in {".jpg", ".jpeg", ".tif", ".tiff"}

    def _raster_tiene_datos_utiles(self, ruta):
        if rasterio is None or not ruta or not os.path.exists(ruta):
            return True

        try:
            with rasterio.open(ruta) as src:
                if src.count < 3 or src.width <= 0 or src.height <= 0:
                    return False

                muestras = [
                    (0, 0),
                    (max(0, src.width // 2 - 128), max(0, src.height // 2 - 128)),
                    (max(0, src.width - 256), max(0, src.height - 256)),
                ]
                for col_off, row_off in muestras:
                    width = min(256, src.width - col_off)
                    height = min(256, src.height - row_off)
                    if width <= 0 or height <= 0:
                        continue
                    ventana = rasterio.windows.Window(col_off, row_off, width, height)
                    bandas = src.read([1, 2, 3], window=ventana)
                    if np.any(bandas > 0):
                        return True
                return False
        except Exception:
            return True

    def _clasificar_dataset(self):
        rgb = []
        ms = []
        for root, dirs, files in os.walk(RUTA_IMAGENES):
            if os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(DATASET_DIR)):
                dirs.clear()
            for nombre in sorted(files):
                ruta = os.path.join(root, nombre)
                if not self._es_imagen(nombre):
                    continue
                ext = os.path.splitext(nombre.lower())[1]
                if ext in {".jpg", ".jpeg"}:
                    rgb.append(ruta)
                elif not self._es_solo_rgb():
                    ms.append(ruta)
        return rgb, ms

    def cargar_fotos(self):
        self._log("[1/5] Cargando fotos")
        Metashape = self._ms()
        doc = self._abrir_documento(crear=True)
        doc.chunks.clear()

        rgb, ms = self._clasificar_dataset()
        if not rgb and not ms:
            raise RuntimeError("No hay fotos validas en dataset/")

        if rgb:
            chunk = doc.addChunk()
            chunk.label = "RGB"
            chunk.addPhotos(rgb)

        if ms:
            chunk = doc.addChunk()
            chunk.label = "MS"
            chunk.addPhotos(ms)
            if self.camera_model == "mavic_3m":
                self._log("[1/5] Calibrando reflectancia MS con el sensor solar")
                if not hasattr(chunk, "calibrateReflectance"):
                    raise RuntimeError("Esta version de Metashape no permite calibrar reflectancia")
                for sensor in chunk.sensors:
                    sensor.normalize_sensitivity = True
                chunk.calibrateReflectance(use_reflectance_panels=False, use_sun_sensor=True)

        self._guardar_documento(RUTA_PROYECTO)

    def alinear_camaras(self):
        self._log("[2/5] Alineando camaras en calidad baja")
        Metashape = self._ms()
        for chunk in self._obtener_chunks():
            if not chunk.cameras:
                continue
            kwargs = {
                "downscale": self.ALIGNMENT_DOWNSCALE,
                "generic_preselection": True,
                "reference_preselection": True,
            }
            chunk.matchPhotos(**kwargs)
            chunk.alignCameras()
            try:
                chunk.resetRegion()
            except Exception:
                pass
        self._guardar_documento()

    def _ruta_dem(self, chunk):
        etiqueta = (getattr(chunk, "label", "") or "").strip().lower()
        if etiqueta == "rgb":
            return RUTA_DEM_SIN_CLASIFICAR

        proyecto_dir = os.path.dirname(RUTA_PROYECTO)
        nombre_base = os.path.splitext(os.path.basename(RUTA_PROYECTO))[0]
        sufijo_chunk = etiqueta or "chunk"
        return os.path.join(proyecto_dir, f"{nombre_base}_{sufijo_chunk}_dem_sin_clasificar.tif")

    def _exportar_dem(self, chunk, elevation, ruta):
        elevation_data = self._enum("DataSource.ElevationData", "ElevationData")
        if elevation_data is None or not hasattr(chunk, "exportRaster"):
            raise RuntimeError("La version de Metashape no admite exportar el DEM")

        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        if os.path.exists(ruta):
            os.remove(ruta)

        elevation_anterior = getattr(chunk, "elevation", None)
        try:
            chunk.elevation = elevation
            chunk.exportRaster(path=ruta, source_data=elevation_data)
        finally:
            chunk.elevation = elevation_anterior

    def construir_mde(self):
        self._log(f"[3/5] Construyendo DEM desde puntos de paso ({self.DEM_RESOLUTION_METERS:g} m/pixel)")
        tie_points_data = self._enum("DataSource.TiePointsData", "TiePointsData")
        if tie_points_data is None:
            raise RuntimeError("La version de Metashape no admite construir el DEM desde puntos de paso")

        for chunk in self._obtener_chunks():
            if not chunk.cameras or (getattr(chunk, "label", "") or "").strip().lower() not in {"rgb", "ms"}:
                continue
            etiqueta = getattr(chunk, "label", "chunk")
            self._log(f"[3/5] Construyendo DEM con puntos de paso: {etiqueta}")
            chunk.buildDem(source_data=tie_points_data, resolution=self.DEM_RESOLUTION_METERS)
            dem = getattr(chunk, "elevation", None)
            if dem is None:
                raise RuntimeError(f"Metashape no creo el DEM de {etiqueta}")
            dem.label = "DEM - Puntos de paso"
            ruta_dem = self._ruta_dem(chunk)
            self._exportar_dem(chunk, dem, ruta_dem)
            self._log(f"[3/5] DEM exportado: {ruta_dem}")
        self._guardar_documento()

    def construir_ortomosaico(self):
        self._log("[4/5] Construyendo ortomosaicos")
        elevation_data = self._enum("DataSource.ElevationData", "ElevationData")
        model_data = self._enum("DataSource.ModelData", "ModelData")
        chunk_rgb = next(
            (item for item in self._obtener_chunks()
             if (getattr(item, "label", "") or "").strip().lower() == "rgb"),
            None,
        )
        for chunk in self._obtener_chunks():
            if not chunk.cameras:
                continue
            etiqueta = (getattr(chunk, "label", "") or "").strip().lower()
            if etiqueta == "ms" and self.camera_model == "mavic_3m":
                dem_ms = getattr(chunk, "elevation", None)
                if elevation_data is None or dem_ms is None:
                    raise RuntimeError("El ortomosaico MS necesita su propio DEM")
                # Igual que RGB: el DEM activo del mismo chunk es la superficie
                # y Metashape decide el mosaico y la resolucion nativa.
                chunk.buildOrthomosaic(
                    surface_data=elevation_data,
                    resolution=self.ORTHOMOSAIC_RESOLUTION_METERS,
                )
                self._log("[4/5] Ortofoto construida con DEM MS propio: MS")
                continue
            if etiqueta == "ms":
                if chunk_rgb is None or not os.path.isfile(RUTA_DEM_SIN_CLASIFICAR):
                    raise RuntimeError("El ortomosaico MS requiere fotos RGB para usar su DEM")
                if elevation_data is None:
                    raise RuntimeError("Metashape no admite importar el DEM RGB")
                opciones_importacion = {
                    "path": RUTA_DEM_SIN_CLASIFICAR,
                    "raster_type": elevation_data,
                    "nodata_value": -32767,
                    "has_nodata_value": True,
                    "replace_asset": False,
                }
                if getattr(chunk_rgb, "crs", None) is not None:
                    opciones_importacion["crs"] = chunk_rgb.crs
                chunk.importRaster(**opciones_importacion)
                if getattr(chunk, "elevation", None) is None:
                    raise RuntimeError("No se pudo activar el DEM RGB en el chunk MS")
                self._log("[4/5] DEM RGB importado en MS para construir el ortomosaico")
            resolucion = 0 if etiqueta == "rgb" else self.ORTHOMOSAIC_RESOLUTION_METERS
            opciones_rgb = {"refine_seamlines": True} if etiqueta == "rgb" else {}
            try:
                kwargs = {"resolution": resolucion, **opciones_rgb}
                if elevation_data is not None:
                    kwargs["surface_data"] = elevation_data
                chunk.buildOrthomosaic(**kwargs)
            except Exception:
                kwargs = {"resolution": resolucion, **opciones_rgb}
                if model_data is not None:
                    kwargs["surface_data"] = model_data
                chunk.buildOrthomosaic(**kwargs)
            calidad = "resolucion nativa y uniones refinadas" if etiqueta == "rgb" else "calidad baja"
            self._log(f"[4/5] Ortofoto construida ({calidad}): {getattr(chunk, 'label', 'chunk')}")
        self._guardar_documento()

    def _proyeccion_utm_ms(self, chunk):
        """Calcula la zona UTM desde la referencia GPS de las fotos MS."""
        Metashape = self._ms()
        epsg_configurado = os.environ.get("METASHAPE_MS_EPSG", "").strip()
        if epsg_configurado:
            epsg = int(epsg_configurado)
        else:
            if chunk.crs is None:
                raise RuntimeError("El chunk MS no tiene CRS; define METASHAPE_MS_EPSG")
            wgs84 = Metashape.CoordinateSystem("EPSG::4326")
            posicion = next(
                (camera.reference.location for camera in chunk.cameras
                 if camera.reference.location is not None),
                None,
            )
            if posicion is None:
                raise RuntimeError("Las fotos MS no tienen GPS; define METASHAPE_MS_EPSG")
            geo = Metashape.CoordinateSystem.transform(posicion, chunk.crs, wgs84)
            lon, lat = float(geo.x), float(geo.y)
            if not (-180 <= lon <= 180 and -80 <= lat <= 84):
                raise RuntimeError("No se pudo determinar una zona UTM valida para el chunk MS")
            zona = min(60, max(1, int((lon + 180) // 6) + 1))
            epsg = (32600 if lat >= 0 else 32700) + zona
        if epsg not in range(32601, 32661) and epsg not in range(32701, 32761):
            raise RuntimeError("METASHAPE_MS_EPSG debe ser un codigo UTM WGS84")
        proyeccion = Metashape.OrthoProjection()
        proyeccion.crs = Metashape.CoordinateSystem(f"EPSG::{epsg}")
        return proyeccion, epsg

    def _opciones_exportacion_ms(self, chunk):
        if self.camera_model != "mavic_3m":
            return {}, None
        transformacion = self._enum(
            "RasterTransformType.RasterTransformValue", "RasterTransformValue"
        )
        formato_tiff = self._enum("ImageFormat.ImageFormatTIFF", "ImageFormatTIFF")
        if transformacion is None or formato_tiff is None:
            raise RuntimeError("Esta version de Metashape no admite TIFF de reflectancia decimal")
        chunk.raster_transform.formula = [f"B{i} / 32768" for i in range(1, 5)]
        chunk.raster_transform.enabled = True
        proyeccion, epsg = self._proyeccion_utm_ms(chunk)
        self._log(f"[5/5] MS: valores radiometricos decimales, EPSG:{epsg}, bandas Green/Red/RedEdge/NIR")
        return {
            "raster_transform": transformacion,
            "image_format": formato_tiff,
            "projection": proyeccion,
            "nodata_value": -10000,
        }, epsg

    def _verificar_exportacion_ms(self, ruta, epsg, radiometric_correction=None, expected_resolution=None, destino=None):
        if rasterio is None:
            raise RuntimeError("Se necesita rasterio para verificar el TIFF MS exportado")
        nombres = ("Green", "Red", "Red edge", "NIR")
        longitudes = (560, 650, 730, 860)
        anchos = (16, 16, 16, 26)
        destino = destino or ruta
        temporal = f"{destino}.tmp.tif"
        publicado = False
        try:
            with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(ruta) as src:
                if src.count != 5 or src.dtypes[0] != "float32" or src.crs is None or src.crs.to_epsg() != epsg:
                    raise RuntimeError(
                        f"TIFF MS inesperado: {src.count} bandas, {src.dtypes[0]}, {src.crs}; "
                        "se esperaban 4 bandas de reflectancia y Alpha en UTM"
                    )
                if expected_resolution is not None and any(
                    abs(valor - expected_resolution) > expected_resolution * 0.1 for valor in src.res
                ):
                    raise RuntimeError(
                        f"El TIFF MS tiene resolucion {src.res} m/pixel; se esperaba {expected_resolution:g}"
                    )
                alto = min(src.height, 512)
                ancho = min(src.width, 512)
                muestra = src.read(out_shape=(5, alto, ancho), resampling=Resampling.nearest)
                huella = np.isfinite(muestra[4]) & (muestra[4] > 0)
                if not np.any(huella):
                    raise RuntimeError("El TIFF MS no contiene pixeles dentro de su huella")
                espectrales = muestra[:4]
                validos = np.isfinite(espectrales).all(axis=0) & (espectrales > 0).all(axis=0)
                if np.count_nonzero(huella & validos) / np.count_nonzero(huella) < 0.99:
                    raise RuntimeError("El TIFF MS tiene huecos en las bandas; los indices quedarian incompletos")

                perfil = src.profile.copy()
                perfil.update(
                    driver="GTiff", count=5, dtype="float32", nodata=-10000,
                    compress="lzw", predictor=3, tiled=True,
                    blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER",
                )
                perfil.pop("photometric", None)
                with rasterio.open(temporal, "w", **perfil) as orto:
                    # PIX4Dfields reconoce cuatro bandas espectrales y Alpha
                    # como una quinta banda, igual que su GeoTIFF de referencia.
                    for _, ventana in orto.block_windows(1):
                        datos = src.read(window=ventana)
                        huella = np.isfinite(datos[4]) & (datos[4] > 0)
                        datos[:, ~huella] = -10000
                        datos[4, huella] = 255
                        orto.write(datos, window=ventana)
                    orto.colorinterp = (
                        ColorInterp.green, ColorInterp.red,
                        ColorInterp.undefined, ColorInterp.undefined,
                        ColorInterp.alpha,
                    )
                    metadatos = {
                        "RadiometricCorrection": radiometric_correction or "not_applied",
                        "RadiometricScale": "B1-B4 / 32768",
                        "BandOrder": "Green,Red,RedEdge,NIR,Alpha",
                        "VegetationIndices": "NDVI=(NIR-Red)/(NIR+Red); NDRE=(NIR-RedEdge)/(NIR+RedEdge); GNDVI=(NIR-Green)/(NIR+Green)",
                    }
                    orto.update_tags(**metadatos)
                    for indice, (nombre, longitud, ancho_banda) in enumerate(zip(nombres, longitudes, anchos), 1):
                        tags = {
                            "BandName": nombre,
                            "CentralWavelength": str(longitud),
                            "WavelengthFWHM": str(ancho_banda),
                        }
                        orto.set_band_description(indice, nombre)
                        orto.update_tags(indice, **tags)
                    orto.set_band_description(5, "Alpha")
                    factores = []
                    factor = 2
                    while min(src.width, src.height) // factor >= 256:
                        factores.append(factor)
                        factor *= 2
                    if factores:
                        orto.build_overviews(factores, Resampling.average)
                        orto.update_tags(ns="rio_overview", resampling="average")
            for intento in range(5):
                try:
                    os.replace(temporal, destino)
                    publicado = True
                    break
                except PermissionError as exc:
                    if intento == 4:
                        raise PermissionError(
                            f"No se pudo publicar el GeoTIFF MS en {destino}. "
                            f"Cierra el archivo en QGIS u otro programa e intenta de nuevo. "
                            f"El GeoTIFF de cuatro bandas quedo disponible en {temporal}"
                        ) from exc
                    time.sleep(1)
            if ruta != destino:
                for sobrante in (ruta, f"{ruta}.aux.xml"):
                    try:
                        os.remove(sobrante)
                    except FileNotFoundError:
                        pass
                    except PermissionError:
                        self._log(f"[5/5] No se pudo borrar el archivo temporal ocupado: {sobrante}")
            # Un PAM XML anterior puede describir bandas de otra exportacion.
            auxiliar_viejo = f"{destino}.aux.xml"
            try:
                os.remove(auxiliar_viejo)
            except FileNotFoundError:
                pass
            except PermissionError:
                self._log(f"[5/5] Cierra en QGIS y elimina el metadato anterior: {auxiliar_viejo}")
        finally:
            if publicado and os.path.exists(temporal):
                os.remove(temporal)

    def exportar_resultado(self):
        self._log("[5/5] Exportando resultado")
        Metashape = self._ms()
        proyecto_dir = os.path.dirname(RUTA_PROYECTO)
        os.makedirs(proyecto_dir, exist_ok=True)
        orthomosaic_data = self._enum("DataSource.OrthomosaicData", "OrthomosaicData")

        destinos = {
            "rgb": RUTA_ORTOMOSAICO_RGB,
            "ms": RUTA_ORTOMOSAICO_MS,
        }
        exportados = []
        usar_roi = os.path.isfile(CLIP_GEOJSON)
        poligonos_shapefile = 0
        if usar_roi:
            with open(CLIP_GEOJSON, encoding="utf-8") as archivo:
                recorte = json.load(archivo)
            if (recorte.get("_recorte") or {}).get("formato") == "shp":
                poligonos_shapefile = sum(
                    1 if (feature.get("geometry") or {}).get("type") == "Polygon"
                    else len((feature.get("geometry") or {}).get("coordinates", []))
                    if (feature.get("geometry") or {}).get("type") == "MultiPolygon"
                    else 0
                    for feature in recorte.get("features", [])
                )
                if not poligonos_shapefile:
                    raise RuntimeError("El shapefile de recorte no contiene poligonos")
        recorte_metashape = usar_roi and not poligonos_shapefile
        limite_exterior = None
        formato_geojson = None
        if recorte_metashape:
            limite_exterior = self._enum("Shape.BoundaryType.OuterBoundary", "Shape.OuterBoundary")
            formato_geojson = self._enum("ShapesFormat.ShapesFormatGeoJSON", "ShapesFormatGeoJSON")
            if limite_exterior is None or formato_geojson is None:
                raise RuntimeError("Esta version de Metashape no permite importar el ROI como limite exterior")

        def _borrar_si_existe(ruta):
            try:
                if ruta and os.path.exists(ruta):
                    os.remove(ruta)
            except Exception:
                pass

        compression = None
        if hasattr(Metashape, "ImageCompression"):
            compression = Metashape.ImageCompression()
            if hasattr(compression, "tiff_big"):
                compression.tiff_big = True
            if hasattr(compression, "tiff_compression") and hasattr(Metashape.ImageCompression, "TiffCompressionDeflate"):
                compression.tiff_compression = Metashape.ImageCompression.TiffCompressionDeflate

        def _exportar_chunk(chunk, destino, *, save_alpha=None, white_background=None, usar_compresion=True, opciones=None):
            if not hasattr(chunk, "exportRaster"):
                return False

            kwargs = {"path": destino}
            kwargs.update(opciones or {})
            kwargs["resolution"] = 0 if (getattr(chunk, "label", "") or "").strip().lower() == "rgb" else self.ORTHOMOSAIC_RESOLUTION_METERS
            if orthomosaic_data is not None:
                kwargs["source_data"] = orthomosaic_data
            if compression is not None and usar_compresion:
                kwargs["image_compression"] = compression
            if save_alpha is not None:
                kwargs["save_alpha"] = save_alpha
            if white_background is not None:
                kwargs["white_background"] = white_background
            kwargs["clip_to_boundary"] = recorte_metashape

            _borrar_si_existe(destino)
            try:
                chunk.exportRaster(**kwargs)
                return True
            except TypeError as exc:
                mensaje = str(exc)
                if "image_compression" in mensaje:
                    kwargs.pop("image_compression", None)
                elif "save_alpha" in mensaje:
                    kwargs.pop("save_alpha", None)
                elif "white_background" in mensaje:
                    kwargs.pop("white_background", None)
                else:
                    raise
                _borrar_si_existe(destino)
                chunk.exportRaster(**kwargs)
                return True

        for chunk in self._obtener_chunks():
            if not chunk.cameras:
                continue

            etiqueta = (getattr(chunk, "label", "") or "").strip().lower()
            destino = destinos.get(etiqueta)
            if destino is None:
                destino = os.path.join(
                    proyecto_dir,
                    f"{os.path.splitext(os.path.basename(RUTA_PROYECTO))[0]}_{etiqueta or 'chunk'}.tif",
                )

            try:
                if recorte_metashape:
                    chunk.importShapes(
                        path=CLIP_GEOJSON,
                        boundary_type=limite_exterior,
                        format=formato_geojson,
                        crs=Metashape.CoordinateSystem("EPSG::4326"),
                    )
                    self._log(f"[5/5] ROI cargado en Metashape como limite exterior: {etiqueta.upper()}")
                opciones_ms, epsg_ms = self._opciones_exportacion_ms(chunk) if etiqueta == "ms" else ({}, None)
                fuente_recorte = (
                    f"{destino}.{uuid.uuid4().hex}.sin_recorte.tif"
                    if poligonos_shapefile else destino
                )
                fuente_ms = (
                    f"{fuente_recorte}.metashape_raw.tif"
                    if etiqueta == "ms" and self.camera_model == "mavic_3m"
                    else fuente_recorte
                )
                exportado = _exportar_chunk(
                    chunk,
                    fuente_ms,
                    save_alpha=True,
                    white_background=False,
                    opciones=opciones_ms,
                )

                if etiqueta == "ms" and exportado and self.camera_model == "mavic_3m":
                    self._verificar_exportacion_ms(
                        fuente_ms, epsg_ms, radiometric_correction="sun_sensor", destino=fuente_recorte
                    )
                    self._log("[5/5] MS: GeoTIFF con 4 bandas Green/Red/RedEdge/NIR y Alpha")

                if etiqueta == "rgb" and exportado and not self._raster_tiene_datos_utiles(fuente_recorte):
                    self._log("[5/5] El TIFF RGB salio vacio; reintentando exportacion sin compresion")
                    exportado = _exportar_chunk(
                        chunk,
                        fuente_recorte,
                        save_alpha=True,
                        white_background=False,
                        usar_compresion=False,
                    )

                if exportado and poligonos_shapefile:
                    base, extension = os.path.splitext(destino)
                    if poligonos_shapefile > 1:
                        for indice in range(poligonos_shapefile):
                            pieza = f"{base}_poligono_{indice + 1:03d}{extension}"
                            recortar_ortomosaico(fuente_recorte, CLIP_GEOJSON, salida=pieza, indice=indice)
                            self._log(f"[5/5] Poligono {indice + 1}/{poligonos_shapefile}: {pieza}")
                    recortar_ortomosaico(fuente_recorte, CLIP_GEOJSON, salida=destino)
                    try:
                        os.remove(fuente_recorte)
                    except PermissionError:
                        self._log(f"[5/5] Archivo temporal ocupado; se conserva: {fuente_recorte}")
                    patron_pieza = re.compile(
                        re.escape(os.path.basename(base)) + r"_poligono_(\d+)" + re.escape(extension) + r"$"
                    )
                    for nombre in os.listdir(os.path.dirname(destino)):
                        coincidencia = patron_pieza.fullmatch(nombre)
                        if coincidencia and int(coincidencia.group(1)) > (poligonos_shapefile if poligonos_shapefile > 1 else 0):
                            os.remove(os.path.join(os.path.dirname(destino), nombre))

                if exportado:
                    if etiqueta == "rgb" and not self._raster_tiene_datos_utiles(destino):
                        raise RuntimeError("El TIFF RGB exportado sigue sin datos utiles")
                    exportados.append(destino)
                    if usar_roi:
                        origen_recorte = "Metashape" if recorte_metashape else "shapefile"
                        self._log(f"[5/5] Ortomosaico {etiqueta.upper()} recortado por {origen_recorte}: {destino}")
                    self._log(f"[5/5] Exportado: {destino}")
                else:
                    self._log(f"[5/5] No se pudo exportar {etiqueta or 'chunk'}: sin exportador compatible")
            except Exception as exc:
                if etiqueta != "ms" or self.camera_model != "mavic_3m":
                    _borrar_si_existe(destino)
                self._log(f"[5/5] No se pudo exportar {etiqueta or 'chunk'}: {exc}")
                raise

        if not exportados:
            raise RuntimeError("No se pudo exportar ningun ortomosaico")

        self._guardar_documento(RUTA_PROYECTO)
