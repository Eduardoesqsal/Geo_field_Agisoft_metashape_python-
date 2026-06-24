import os
from collections import defaultdict

import numpy as np
from PIL import Image

try:
    import rasterio
except Exception:
    rasterio = None

from backend.config import RUTA_IMAGENES, RUTA_ORTOMOSAICO_MS, RUTA_ORTOMOSAICO_RGB, RUTA_PROYECTO


class ProcesamientoMetashape:
    REDEDGE_RGB_STRETCH = (2, 98)
    REDEDGE_RGB_GAMMA = 1.15

    def __init__(self, progress_callback=None, camera_model=None):
        self.doc = None
        self.progress_callback = progress_callback
        self._metashape = None
        self.camera_model = self._normalizar_modelo_camara(camera_model)
        self._tempdirs = []

    def _normalizar_modelo_camara(self, camera_model):
        modelo = (camera_model or "mavic_3m").strip().lower().replace(" ", "_")
        alias = {
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
        return alias.get(modelo, "mavic_3m")

    def _ms(self):
        if self._metashape is None:
            try:
                import Metashape as metashape
            except ModuleNotFoundError as exc:
                raise ModuleNotFoundError("Usa el Python de Agisoft Metashape.") from exc
            self._metashape = metashape
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
            else:
                ms.append(ruta)
        return rgb, ms

    def cargar_fotos(self):
        self._log("[1/6] Cargando fotos")
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
        self._log("[2/6] Alineando camaras")
        Metashape = self._ms()
        accuracy = self._enum(
            "Accuracy.HighAccuracy",
            "Accuracy.HighestAccuracy",
            "HighAccuracy",
            "HighestAccuracy",
            "Accuracy.High",
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
        self._guardar_documento()

    def construir_profundidad(self):
        self._log("[3/6] Construyendo profundidad")
        Metashape = self._ms()
        filter_mode = self._enum(
            "FilterMode.MildFiltering",
            "MildFiltering",
            "FilterMode.AggressiveFiltering",
        )
        for chunk in self._obtener_chunks():
            if not chunk.cameras:
                continue
            kwargs = {"downscale": 2}
            if filter_mode is not None:
                kwargs["filter_mode"] = filter_mode
            chunk.buildDepthMaps(**kwargs)
        self._guardar_documento()

    def construir_modelo(self):
        self._log("[4/6] Construyendo modelo")
        Metashape = self._ms()
        surface_type = self._enum("SurfaceType.Arbitrary", "Arbitrary")
        depth_maps = self._enum("DataSource.DepthMapsData", "DepthMapsData")
        point_cloud = self._enum("DataSource.PointCloudData", "PointCloudData")
        for chunk in self._obtener_chunks():
            if not chunk.cameras:
                continue
            try:
                kwargs = {}
                if surface_type is not None:
                    kwargs["surface_type"] = surface_type
                if depth_maps is not None:
                    kwargs["source_data"] = depth_maps
                chunk.buildModel(**kwargs)
            except Exception:
                kwargs = {}
                if surface_type is not None:
                    kwargs["surface_type"] = surface_type
                if point_cloud is not None:
                    kwargs["source_data"] = point_cloud
                chunk.buildModel(**kwargs)
        self._guardar_documento()

    def construir_ortomosaico(self):
        self._log("[5/6] Construyendo ortomosaico")
        Metashape = self._ms()
        model_data = self._enum("DataSource.ModelData", "ModelData")
        elevation_data = self._enum("DataSource.ElevationData", "ElevationData")
        for chunk in self._obtener_chunks():
            if not chunk.cameras:
                continue
            try:
                kwargs_dem = {}
                if model_data is not None:
                    kwargs_dem["source_data"] = model_data
                chunk.buildDem(**kwargs_dem)
                kwargs_ortho = {}
                if model_data is not None:
                    kwargs_ortho["surface_data"] = model_data
                chunk.buildOrthomosaic(**kwargs_ortho)
            except Exception:
                kwargs = {}
                if elevation_data is not None:
                    kwargs["surface_data"] = elevation_data
                chunk.buildOrthomosaic(**kwargs)
        self._guardar_documento()

    def exportar_resultado(self):
        self._log("[6/6] Exportando resultado")
        Metashape = self._ms()
        proyecto_dir = os.path.dirname(RUTA_PROYECTO)
        os.makedirs(proyecto_dir, exist_ok=True)
        orthomosaic_data = self._enum("DataSource.OrthomosaicData", "OrthomosaicData")

        destinos = {
            "rgb": RUTA_ORTOMOSAICO_RGB,
            "ms": RUTA_ORTOMOSAICO_MS,
        }
        exportados = []

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
                if hasattr(chunk, "exportOrthomosaic"):
                    try:
                        chunk.exportOrthomosaic(path=destino)
                    except TypeError:
                        kwargs = {"path": destino}
                        if orthomosaic_data is not None:
                            kwargs["source_data"] = orthomosaic_data
                        chunk.exportOrthomosaic(**kwargs)
                elif hasattr(chunk, "exportRaster"):
                    kwargs = {"path": destino}
                    if orthomosaic_data is not None:
                        kwargs["source_data"] = orthomosaic_data
                    chunk.exportRaster(**kwargs)
                else:
                    continue
                exportados.append(destino)
                self._log(f"[6/6] Exportado: {destino}")
            except Exception as exc:
                self._log(f"[6/6] No se pudo exportar {etiqueta or 'chunk'}: {exc}")

        if not exportados:
            raise RuntimeError("No se pudo exportar ningun ortomosaico")

        self._guardar_documento(RUTA_PROYECTO)
