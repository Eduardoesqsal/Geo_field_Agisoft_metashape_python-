import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from backend.workflow import ProcesamientoMetashape


class ExportacionRoiNativoTest(unittest.TestCase):
    def test_construye_dem_completo_y_filtrado_en_rgb_y_ms(self):
        resultados = {}

        def crear_chunk(etiqueta):
            dem_completo = SimpleNamespace(key=f"{etiqueta}-completo", label="")
            dem_filtrado = SimpleNamespace(key=f"{etiqueta}-filtrado", label="")
            chunk = SimpleNamespace(
                cameras=[object()], label=etiqueta, elevation=None, elevations=[],
                point_cloud=SimpleNamespace(classifyGroundPoints=lambda **_: None),
            )

            def construir_dem(**opciones):
                dem = dem_filtrado if opciones.get("classes") else dem_completo
                chunk.elevations.append(dem)
                chunk.elevation = dem
                resultados.setdefault(etiqueta, []).append(opciones)

            chunk.buildDem = construir_dem
            return chunk

        chunks = [crear_chunk("RGB"), crear_chunk("MS")]
        app = ProcesamientoMetashape(camera_model="mavic_3m")
        app._metashape = SimpleNamespace(
            DataSource=SimpleNamespace(PointCloudData="nube"),
            PointClass=SimpleNamespace(Ground="suelo"),
        )
        app._obtener_chunks = lambda: chunks
        app._exportar_dem = lambda *_: None
        app._guardar_documento = lambda *_: None

        app.construir_mde()

        for chunk in chunks:
            self.assertEqual(len(chunk.elevations), 2)
            self.assertEqual(chunk.elevation.label, "DEM - Suelo clasificado (Ground)")
            self.assertEqual(resultados[chunk.label][0], {"source_data": "nube"})
            self.assertEqual(resultados[chunk.label][1]["classes"], ["suelo"])

    def test_ms_construye_ortomosaico_con_su_dem_como_rgb(self):
        llamadas_rgb = []
        llamadas_ms = []
        dem_ms = SimpleNamespace(label="DEM - Suelo clasificado (Ground)")
        rgb = SimpleNamespace(cameras=[object()], label="RGB",
                              buildOrthomosaic=lambda **kwargs: llamadas_rgb.append(kwargs))
        ms = SimpleNamespace(
            cameras=[object()],
            label="MS",
            elevation=dem_ms,
            buildOrthomosaic=lambda **kwargs: llamadas_ms.append(kwargs),
        )
        app = ProcesamientoMetashape(camera_model="mavic_3m")
        app._metashape = SimpleNamespace(
            DataSource=SimpleNamespace(ElevationData="elevacion", ModelData="modelo"),
        )
        app._obtener_chunks = lambda: [rgb, ms]
        app._guardar_documento = lambda *_: None

        app.construir_ortomosaico()

        self.assertIs(ms.elevation, dem_ms)
        self.assertEqual(llamadas_rgb, [{"surface_data": "elevacion"}])
        self.assertEqual(llamadas_ms, llamadas_rgb)

    def test_ms_exporta_reflectancia_y_verifica_bandas(self):
        exportaciones = []
        verificaciones = []
        chunk = SimpleNamespace(
            cameras=[object()],
            label="MS",
            exportRaster=lambda **kwargs: exportaciones.append(kwargs),
        )
        app = ProcesamientoMetashape(camera_model="mavic_3m")
        app._metashape = SimpleNamespace(
            DataSource=SimpleNamespace(OrthomosaicData="ortomosaico"),
        )
        app._obtener_chunks = lambda: [chunk]
        app._opciones_exportacion_ms = lambda _: ({"raster_transform": "reflectancia"}, 32614)
        app._verificar_exportacion_ms = lambda *args, **kwargs: verificaciones.append((args, kwargs))
        app._guardar_documento = lambda *_: None

        with tempfile.TemporaryDirectory() as carpeta:
            salida = os.path.join(carpeta, "ms.tif")
            with patch("backend.workflow.CLIP_GEOJSON", os.path.join(carpeta, "sin_roi.geojson")), \
                 patch("backend.workflow.RUTA_ORTOMOSAICO_MS", salida), \
                 patch("backend.workflow.RUTA_PROYECTO", os.path.join(carpeta, "prueba.psx")):
                app.exportar_resultado()

        self.assertEqual(exportaciones[0]["raster_transform"], "reflectancia")
        self.assertNotIn("resolution", exportaciones[0])
        self.assertEqual(exportaciones[0]["path"], salida + ".metashape_raw.tif")
        self.assertEqual(verificaciones[0][0], (salida + ".metashape_raw.tif", 32614))
        self.assertEqual(verificaciones[0][1]["radiometric_correction"], "sun_sensor")
        self.assertEqual(verificaciones[0][1]["destino"], salida)

    def test_verificacion_ms_rechaza_huecos_entre_bandas(self):
        app = ProcesamientoMetashape(camera_model="mavic_3m")
        with tempfile.TemporaryDirectory() as carpeta:
            destino = os.path.join(carpeta, "ms.tif")
            ruta = destino + ".metashape_raw.tif"
            datos = np.ones((5, 64, 64), dtype=np.float32)
            datos[1, :32, :] = 0
            with rasterio.open(
                ruta, "w", driver="GTiff", width=64, height=64, count=5,
                dtype="float32", crs="EPSG:32614", transform=from_origin(0, 10, 0.04, 0.04),
            ) as dst:
                dst.write(datos)
            with self.assertRaisesRegex(RuntimeError, "indices quedarian incompletos"):
                app._verificar_exportacion_ms(ruta, 32614, destino=destino)
            datos[1] = 1
            datos[4, 0, 0] = 0
            with rasterio.open(ruta, "r+") as dst:
                dst.write(datos)
            reemplazar = os.replace
            intentos = []

            def reemplazo_temporal(origen, salida):
                intentos.append(1)
                if len(intentos) == 1:
                    raise PermissionError("archivo ocupado")
                reemplazar(origen, salida)

            with patch("backend.workflow.os.replace", side_effect=reemplazo_temporal), \
                 patch("backend.workflow.time.sleep"):
                app._verificar_exportacion_ms(ruta, 32614, destino=destino)
            self.assertEqual(len(intentos), 2)
            self.assertFalse(os.path.exists(ruta))
            with rasterio.open(destino) as src:
                self.assertEqual(src.count, 5)
                self.assertEqual(src.nodata, -10000)
                self.assertEqual(src.crs.to_epsg(), 32614)
                self.assertEqual(src.compression, rasterio.enums.Compression.lzw)
                self.assertEqual(src.block_shapes[0], (256, 256))
                self.assertEqual(src.descriptions, ("Green", "Red", "Red edge", "NIR", "Alpha"))
                self.assertIn("NDVI=", src.tags()["VegetationIndices"])
                self.assertEqual(src.dataset_mask()[32, 32], 255)
                self.assertEqual(src.dataset_mask()[0, 0], 0)
                self.assertEqual(src.read(4)[0, 0], -10000)
                self.assertEqual(src.read(5)[32, 32], 255)
                self.assertEqual(src.read(5)[0, 0], -10000)
                self.assertEqual(src.read(2)[32, 32], 1)
                self.assertEqual(src.colorinterp[4], rasterio.enums.ColorInterp.alpha)


    def test_roi_se_importa_y_recorta_en_exportacion_metashape(self):
        importaciones = []
        exportaciones = []
        chunk = SimpleNamespace(
            cameras=[object()],
            label="RGB",
            importShapes=lambda **kwargs: importaciones.append(kwargs),
            exportRaster=lambda **kwargs: exportaciones.append(kwargs),
        )
        metashape = SimpleNamespace(
            DataSource=SimpleNamespace(OrthomosaicData="ortomosaico"),
            Shape=SimpleNamespace(BoundaryType=SimpleNamespace(OuterBoundary="exterior")),
            ShapesFormat=SimpleNamespace(ShapesFormatGeoJSON="geojson"),
            CoordinateSystem=lambda crs: crs,
        )
        app = ProcesamientoMetashape()
        app._metashape = metashape
        app._obtener_chunks = lambda: [chunk]
        app._raster_tiene_datos_utiles = lambda _: True
        app._guardar_documento = lambda *_: None

        with tempfile.TemporaryDirectory() as carpeta:
            roi = os.path.join(carpeta, "roi.geojson")
            salida = os.path.join(carpeta, "prueba_rgb.tif")
            with open(roi, "w", encoding="utf-8") as archivo:
                archivo.write('{"type":"FeatureCollection","features":[]}')
            with patch("backend.workflow.CLIP_GEOJSON", roi), \
                 patch("backend.workflow.RUTA_ORTOMOSAICO_RGB", salida), \
                 patch("backend.workflow.RUTA_PROYECTO", os.path.join(carpeta, "prueba.psx")):
                app.exportar_resultado()

        self.assertEqual(importaciones[0]["boundary_type"], "exterior")
        self.assertEqual(importaciones[0]["format"], "geojson")
        self.assertEqual(importaciones[0]["crs"], "EPSG::4326")
        self.assertTrue(exportaciones[0]["clip_to_boundary"])
        self.assertTrue(exportaciones[0]["save_alpha"])


if __name__ == "__main__":
    unittest.main()
