import os
from collections import defaultdict

import numpy as np
from PIL import Image

try:
    import rasterio
except Exception:
    rasterio = None

from backend.config import (
    RUTA_CURVAS_NIVEL,
    RUTA_DEM_SIN_CLASIFICAR,
    RUTA_DEM_SUELO,
    RUTA_IMAGENES,
    RUTA_ORTOMOSAICO_MS,
    RUTA_ORTOMOSAICO_RGB,
    RUTA_PROYECTO,
)


class ProcesamientoMetashape:
    REDEDGE_RGB_STRETCH = (2, 98)
    REDEDGE_RGB_GAMMA = 1.15
    DEPTH_MAP_DOWNSCALE = 4
    # Perfil conservador: prioriza un MDT limpio aunque deje huecos que
    # Metashape deba interpolar. Todos los valores estan expresados en las
    # unidades que usa classifyGroundPoints (grados y metros).
    GROUND_MAX_ANGLE = 8.0
    GROUND_MAX_DISTANCE = 0.35
    GROUND_MAX_TERRAIN_SLOPE = 25.0
    GROUND_CELL_SIZE = 15.0
    GROUND_EROSION_RADIUS = 0.50

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

    def _parametro_suelo(self, nombre, predeterminado, minimo=0.0):
        """Permite afinar el filtro sin modificar el codigo del pipeline."""
        variable = f"METASHAPE_GROUND_{nombre}"
        valor = os.environ.get(variable)
        if valor in (None, ""):
            return predeterminado
        try:
            numero = float(valor)
        except ValueError as exc:
            raise RuntimeError(f"{variable} debe ser un numero; recibido: {valor}") from exc
        if numero < minimo:
            raise RuntimeError(f"{variable} debe ser mayor o igual que {minimo:g}")
        return numero

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
        for nombre in sorted(os.listdir(RUTA_IMAGENES)):
            ruta = os.path.join(RUTA_IMAGENES, nombre)
            if not os.path.isfile(ruta) or not self._es_imagen(nombre):
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

        self._guardar_documento(RUTA_PROYECTO)

    def alinear_camaras(self):
        self._log("[2/5] Alineando camaras")
        Metashape = self._ms()
        accuracy = self._enum(
            "Accuracy.LowAccuracy",
            "LowAccuracy",
            "Accuracy.Low",
        )
        for chunk in self._obtener_chunks():
            if not chunk.cameras:
                continue
            kwargs = {
                "generic_preselection": True,
                "reference_preselection": True,
            }
            if accuracy is not None:
                kwargs["accuracy"] = accuracy
            chunk.matchPhotos(**kwargs)
            chunk.alignCameras()
            try:
                chunk.resetRegion()
            except Exception:
                pass
        self._guardar_documento()

    def _tiene_nube_de_puntos(self, chunk):
        cloud = getattr(chunk, "point_cloud", None)
        if cloud is not None:
            return True
        nubes = getattr(chunk, "point_clouds", None)
        return bool(nubes)

    def _ruta_dem(self, chunk, tipo):
        etiqueta = (getattr(chunk, "label", "") or "").strip().lower()
        if etiqueta == "rgb":
            return RUTA_DEM_SUELO if tipo == "suelo" else RUTA_DEM_SIN_CLASIFICAR

        proyecto_dir = os.path.dirname(RUTA_PROYECTO)
        nombre_base = os.path.splitext(os.path.basename(RUTA_PROYECTO))[0]
        sufijo_chunk = etiqueta or "chunk"
        return os.path.join(proyecto_dir, f"{nombre_base}_{sufijo_chunk}_dem_{tipo}.tif")

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

    def _dem_nuevo(self, chunk, claves_anteriores):
        elevaciones = list(getattr(chunk, "elevations", []) or [])
        for elevation in reversed(elevaciones):
            clave = getattr(elevation, "key", id(elevation))
            if clave not in claves_anteriores:
                return elevation
        return getattr(chunk, "elevation", None)

    def construir_mde(self):
        self._log("[3/5] Construyendo DEM sin clasificar y DEM de suelo")
        point_cloud_data = self._enum("DataSource.PointCloudData", "PointCloudData")
        ground_class = self._enum("PointClass.Ground")
        mild_filtering = self._enum("FilterMode.MildFiltering", "MildFiltering")
        if point_cloud_data is None or ground_class is None:
            raise RuntimeError("La version de Metashape no admite un MDT filtrado por clase Ground")

        for chunk in self._obtener_chunks():
            if not chunk.cameras:
                continue
            etiqueta = getattr(chunk, "label", "chunk")
            if not self._tiene_nube_de_puntos(chunk):
                self._log(f"[3/5] Generando mapas de profundidad: {etiqueta}")
                depth_kwargs = {"downscale": self.DEPTH_MAP_DOWNSCALE}
                if mild_filtering is not None:
                    depth_kwargs["filter_mode"] = mild_filtering
                chunk.buildDepthMaps(**depth_kwargs)

                self._log(f"[3/5] Generando nube de puntos densa: {etiqueta}")
                chunk.buildPointCloud(point_colors=True, point_confidence=True)

            cloud = getattr(chunk, "point_cloud", None)
            if cloud is None or not hasattr(cloud, "classifyGroundPoints"):
                raise RuntimeError(f"No se pudo crear o clasificar la nube de puntos de {etiqueta}")

            self._log(f"[3/5] Construyendo DEM con todos los puntos: {etiqueta}")
            chunk.buildDem(source_data=point_cloud_data)
            dem_sin_clasificar = chunk.elevation
            dem_sin_clasificar.label = "DEM - Todos los puntos (sin clasificar)"
            ruta_sin_clasificar = self._ruta_dem(chunk, "sin_clasificar")
            self._exportar_dem(chunk, dem_sin_clasificar, ruta_sin_clasificar)
            self._log(f"[3/5] DEM sin clasificar exportado: {ruta_sin_clasificar}")

            self._log(f"[3/5] Clasificando puntos de terreno natural: {etiqueta}")
            classification_kwargs = {
                "max_angle": self._parametro_suelo("MAX_ANGLE", self.GROUND_MAX_ANGLE),
                "max_distance": self._parametro_suelo("MAX_DISTANCE", self.GROUND_MAX_DISTANCE),
                "max_terrain_slope": self._parametro_suelo(
                    "MAX_TERRAIN_SLOPE", self.GROUND_MAX_TERRAIN_SLOPE
                ),
                "cell_size": self._parametro_suelo("CELL_SIZE", self.GROUND_CELL_SIZE, minimo=0.01),
                "erosion_radius": self._parametro_suelo(
                    "EROSION_RADIUS", self.GROUND_EROSION_RADIUS
                ),
                "keep_existing": False,
            }
            self._log(
                "[3/5] Filtro Ground: "
                f"angulo={classification_kwargs['max_angle']:g} grados, "
                f"distancia={classification_kwargs['max_distance']:g} m, "
                f"pendiente={classification_kwargs['max_terrain_slope']:g} grados, "
                f"celda={classification_kwargs['cell_size']:g} m, "
                f"erosion={classification_kwargs['erosion_radius']:g} m"
            )
            try:
                cloud.classifyGroundPoints(**classification_kwargs)
            except TypeError as exc:
                # Metashape 2.0/2.1 no incluye max_terrain_slope.
                if "max_terrain_slope" not in str(exc):
                    raise
                classification_kwargs.pop("max_terrain_slope")
                cloud.classifyGroundPoints(**classification_kwargs)

            puntos_por_clase = getattr(cloud, "point_count_by_class", None)
            if isinstance(puntos_por_clase, dict) and puntos_por_clase.get(ground_class) == 0:
                raise RuntimeError(f"La clasificacion no encontro puntos de terreno en {etiqueta}")

            self._log(f"[3/5] Construyendo MDT solo con clase Ground: {etiqueta}")
            claves_dem_anteriores = {
                getattr(elevation, "key", id(elevation))
                for elevation in (getattr(chunk, "elevations", []) or [])
            }
            chunk.buildDem(
                source_data=point_cloud_data,
                classes=[ground_class],
                replace_asset=False,
            )
            dem_suelo = self._dem_nuevo(chunk, claves_dem_anteriores)
            if dem_suelo is None:
                raise RuntimeError(f"Metashape no creo el DEM de suelo para {etiqueta}")
            dem_suelo.label = "DEM - Suelo clasificado (Ground)"
            ruta_suelo = self._ruta_dem(chunk, "suelo")
            self._exportar_dem(chunk, dem_suelo, ruta_suelo)
            # El DEM de suelo debe quedar activo para ortomosaico y curvas.
            chunk.elevation = dem_suelo
            self._log(f"[3/5] DEM de suelo exportado y activado: {ruta_suelo}")
        self._guardar_documento()

    def construir_ortomosaico(self):
        self._log("[4/5] Construyendo ortomosaico")
        Metashape = self._ms()
        elevation_data = self._enum("DataSource.ElevationData", "ElevationData")
        model_data = self._enum("DataSource.ModelData", "ModelData")
        for chunk in self._obtener_chunks():
            if not chunk.cameras:
                continue
            try:
                kwargs = {}
                if elevation_data is not None:
                    kwargs["surface_data"] = elevation_data
                chunk.buildOrthomosaic(**kwargs)
            except Exception:
                kwargs = {}
                if model_data is not None:
                    kwargs["surface_data"] = model_data
                chunk.buildOrthomosaic(**kwargs)
            self._log(f"[4/5] Ortofoto construida: {getattr(chunk, 'label', 'chunk')}")
        self._guardar_documento()

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

        def _exportar_chunk(chunk, destino, *, save_alpha=None, white_background=None, usar_compresion=True):
            if not hasattr(chunk, "exportRaster"):
                return False

            kwargs = {"path": destino}
            if orthomosaic_data is not None:
                kwargs["source_data"] = orthomosaic_data
            if compression is not None and usar_compresion:
                kwargs["image_compression"] = compression
            if save_alpha is not None:
                kwargs["save_alpha"] = save_alpha
            if white_background is not None:
                kwargs["white_background"] = white_background

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
                exportado = _exportar_chunk(
                    chunk,
                    destino,
                    save_alpha=True,
                    white_background=False,
                )

                if etiqueta == "rgb" and exportado and not self._raster_tiene_datos_utiles(destino):
                    self._log("[5/5] El TIFF RGB salio vacio; reintentando exportacion sin compresion")
                    exportado = _exportar_chunk(
                        chunk,
                        destino,
                        save_alpha=True,
                        white_background=False,
                        usar_compresion=False,
                    )

                if exportado:
                    if etiqueta == "rgb" and not self._raster_tiene_datos_utiles(destino):
                        raise RuntimeError("El TIFF RGB exportado sigue sin datos utiles")
                    exportados.append(destino)
                    self._log(f"[5/5] Exportado (con canal alfa): {destino}")
                else:
                    self._log(f"[5/5] No se pudo exportar {etiqueta or 'chunk'}: sin exportador compatible")
            except Exception as exc:
                self._log(f"[5/5] No se pudo exportar {etiqueta or 'chunk'}: {exc}")

        if not exportados:
            raise RuntimeError("No se pudo exportar ningun ortomosaico")

        self._guardar_documento(RUTA_PROYECTO)

    def generar_curvas_nivel(self, intervalo_m=5, intervalo_maestra_m=25):
        """Construye curvas delgadas; el backend clasifica las maestras."""
        self._log(
            f"[7/7] Generando curvas delgadas cada {intervalo_m:g} m "
            f"y maestras cada {intervalo_maestra_m:g} m"
        )
        Metashape = self._ms()
        elevation_data = self._enum("DataSource.ElevationData", "ElevationData")
        geojson_format = self._enum("ShapesFormat.ShapesFormatGeoJSON", "ShapesFormatGeoJSON")
        if elevation_data is None or geojson_format is None:
            raise RuntimeError("La API de Metashape no admite curvas o exportacion GeoJSON")

        os.makedirs(os.path.dirname(RUTA_CURVAS_NIVEL), exist_ok=True)
        if os.path.exists(RUTA_CURVAS_NIVEL):
            os.remove(RUTA_CURVAS_NIVEL)

        # La superficie de referencia es el MDE generado en el paso anterior.
        # Preferimos RGB porque es el chunk principal cuando hay datos mixtos.
        chunks = list(self._obtener_chunks())
        chunks.sort(key=lambda chunk: 0 if (getattr(chunk, "label", "") or "").strip().lower() == "rgb" else 1)
        chunk = next((item for item in chunks if getattr(item, "elevation", None) is not None), None)
        if chunk is None:
            raise RuntimeError("No existe un MDE disponible para generar curvas")

        self._log("[7/7] Usando MDE de suelo ya construido; generando curvas")

        # En Metashape 2.2.x leer chunk.shapes puede lanzar OSError si el
        # almacén todavía no existe; no devuelve None. Inicializamos la capa
        # explícitamente para que Metashape cree shapes.zip al guardar.
        chunk.shapes = Metashape.Shapes()
        self._log("[7/7] Almacen de curvas inicializado")

        chunk.buildContours(
            source_data=elevation_data,
            interval=float(intervalo_m),
            prevent_intersections=True,
        )
        shapes = chunk.shapes
        if not shapes.shapes:
            raise RuntimeError("Metashape no genero geometria de curvas de nivel")

        crs_wgs84 = Metashape.CoordinateSystem("EPSG::4326")
        chunk.exportShapes(
            path=RUTA_CURVAS_NIVEL,
            save_polylines=True,
            format=geojson_format,
            crs=crs_wgs84,
            save_labels=True,
            save_attributes=True,
        )
        self._log(f"[7/7] Curvas exportadas: {RUTA_CURVAS_NIVEL}")
