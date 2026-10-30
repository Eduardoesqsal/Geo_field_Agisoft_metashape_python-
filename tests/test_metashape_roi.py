import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from backend.workflow import ProcesamientoMetashape
from backend.services.overlay import _mapear_bandas_rgb


class ExportacionRoiNativoTest(unittest.TestCase):
    def test_alinea_rgb_en_calidad_alta_y_omite_nube_y_mapas_de_profundidad(self):
        llamadas = {}
        chunk = SimpleNamespace(
            cameras=[object()], label="RGB",
            matchPhotos=lambda **kwargs: llamadas.setdefault("alineacion", kwargs),
            alignCameras=lambda: None,
            resetRegion=lambda: None,
            buildDepthMaps=lambda **kwargs: self.fail("No debe generar mapas de profundidad"),
            buildPointCloud=lambda **kwargs: self.fail("No debe generar nube de puntos"),
            buildDem=lambda **kwargs: (llamadas.setdefault("dem", kwargs), setattr(chunk, "elevation", SimpleNamespace(label=""))),
        )
        app = ProcesamientoMetashape()
        app._metashape = SimpleNamespace(
            DataSource=SimpleNamespace(TiePointsData="puntos_de_paso"),
        )
        app._obtener_chunks = lambda: [chunk]
        app._exportar_dem = lambda *_: None
        app._guardar_documento = lambda *_: None

        app.alinear_camaras()
        app.construir_mde()

        self.assertEqual(llamadas["alineacion"]["downscale"], 1)
        self.assertEqual(llamadas["dem"]["source_data"], "puntos_de_paso")
        self.assertEqual(llamadas["dem"]["resolution"], 0.5)
        self.assertEqual(chunk.elevation.label, "DEM - Puntos de paso")

    def test_rededge_reintenta_alineacion_y_extrapola_solo_su_dem(self):
        llamadas_match = []
        llamadas_align = []
        llamadas_dem = []
        camaras = [SimpleNamespace(master=None, enabled=True, transform=None) for _ in range(10)]
        chunk = SimpleNamespace(cameras=camaras, label="MS", elevation=None,
                                resetRegion=lambda: None)

        def match_photos(**kwargs):
            llamadas_match.append(kwargs)

        def align_cameras(**kwargs):
            llamadas_align.append(kwargs)
            for camera in camaras[:5 if len(llamadas_align) == 1 else 10]:
                camera.transform = object()

        def build_dem(**kwargs):
            llamadas_dem.append(kwargs)
            chunk.elevation = SimpleNamespace(label="")

        chunk.matchPhotos = match_photos
        chunk.alignCameras = align_cameras
        chunk.buildDem = build_dem
        app = ProcesamientoMetashape(camera_model="rededge_m")
        app._metashape = SimpleNamespace(
            DataSource=SimpleNamespace(TiePointsData="puntos_de_paso"),
            Interpolation=SimpleNamespace(Extrapolated="extrapolado"),
        )
        app._obtener_chunks = lambda: [chunk]
        app._guardar_documento = lambda *_: None
        app._exportar_dem = lambda *_: None

        app.alinear_camaras()
        app.construir_mde()

        self.assertEqual(llamadas_match[0]["downscale"], 2)
        self.assertFalse(llamadas_match[0]["reference_preselection"])
        self.assertEqual(llamadas_match[1]["downscale"], 1)
        self.assertTrue(llamadas_match[1]["reset_matches"])
        self.assertEqual(llamadas_align, [{}, {"reset_alignment": True}])
        self.assertEqual(llamadas_dem[0]["interpolation"], "extrapolado")

    def test_rededge_cuenta_camaras_master_que_apuntan_a_si_mismas(self):
        principales = [SimpleNamespace(key=i, enabled=True, transform=object()) for i in range(10)]
        camaras = []
        for principal in principales:
            principal.master = principal
            camaras.extend([principal, SimpleNamespace(master=principal, enabled=True, transform=None)])
        llamadas_match = []
        chunk = SimpleNamespace(cameras=camaras, label="MS",
                                matchPhotos=lambda **kwargs: llamadas_match.append(kwargs),
                                alignCameras=lambda **kwargs: None,
                                resetRegion=lambda: None)
        app = ProcesamientoMetashape(camera_model="rededge_m")
        app._obtener_chunks = lambda: [chunk]
        app._metashape = SimpleNamespace()
        app._guardar_documento = lambda *_: None

        app.alinear_camaras()

        self.assertEqual(len(llamadas_match), 1)
        self.assertEqual(llamadas_match[0]["downscale"], 2)

    def test_rededge_avisa_si_casi_la_mitad_no_se_alinea(self):
        camaras = [SimpleNamespace(master=None, enabled=True, transform=None) for _ in range(10)]
        chunk = SimpleNamespace(cameras=camaras, label="MS",
                                matchPhotos=lambda **kwargs: None,
                                alignCameras=lambda **kwargs: None,
                                resetRegion=lambda: None)
        app = ProcesamientoMetashape(camera_model="rededge_m")
        app._obtener_chunks = lambda: [chunk]
        app._metashape = SimpleNamespace()
        app._guardar_documento = lambda *_: None
        avisos = []
        app._log = avisos.append

        app.alinear_camaras()

        self.assertTrue(any("revisa el solape" in aviso for aviso in avisos))

    def test_shapefile_exporta_una_pieza_por_poligono(self):
        exportaciones = []

        def exportar(**opciones):
            exportaciones.append(opciones)
            with rasterio.open(
                opciones["path"], "w", driver="GTiff", width=10, height=4,
                count=3, dtype="uint8", crs="EPSG:4326",
                transform=from_origin(0, 4, 1, 1),
            ) as raster:
                raster.write(np.full((3, 4, 10), 100, dtype="uint8"))

        chunk = SimpleNamespace(cameras=[object()], label="RGB", exportRaster=exportar)
        app = ProcesamientoMetashape()
        app._metashape = SimpleNamespace(DataSource=SimpleNamespace(OrthomosaicData="ortomosaico"))
        app._obtener_chunks = lambda: [chunk]
        app._raster_tiene_datos_utiles = lambda _: True
        app._guardar_documento = lambda *_: None

        with tempfile.TemporaryDirectory() as carpeta:
            roi = os.path.join(carpeta, "roi.geojson")
            salida = os.path.join(carpeta, "prueba_rgb.tif")
            geometrias = [
                {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]},
                {"type": "Polygon", "coordinates": [[[8, 2], [10, 2], [10, 4], [8, 4], [8, 2]]]},
            ]
            with open(roi, "w", encoding="utf-8") as archivo:
                json.dump({"type": "FeatureCollection", "features": [{
                    "type": "Feature", "properties": {},
                    "geometry": {"type": "MultiPolygon", "coordinates": [
                        geometria["coordinates"] for geometria in geometrias
                    ]},
                }], "_recorte": {"formato": "shp"}}, archivo)
            pieza_antigua = os.path.join(carpeta, "prueba_rgb_poligono_003.tif")
            with open(pieza_antigua, "wb") as archivo:
                archivo.write(b"anterior")
            with patch("backend.workflow.CLIP_GEOJSON", roi), \
                 patch("backend.workflow.RUTA_ORTOMOSAICO_RGB", salida), \
                 patch("backend.workflow.RUTA_PROYECTO", os.path.join(carpeta, "prueba.psx")):
                app.exportar_resultado()

            self.assertFalse(exportaciones[0]["clip_to_boundary"])
            self.assertEqual(exportaciones[0]["resolution"], 0)
            self.assertNotEqual(exportaciones[0]["path"], salida)
            self.assertFalse(os.path.exists(exportaciones[0]["path"]))
            self.assertFalse(os.path.exists(pieza_antigua))
            for indice in (1, 2):
                pieza = os.path.join(carpeta, f"prueba_rgb_poligono_{indice:03d}.tif")
                with rasterio.open(pieza) as raster:
                    self.assertEqual((raster.width, raster.height), (2, 2))
            with rasterio.open(salida) as raster:
                self.assertEqual((raster.width, raster.height), (10, 4))
                self.assertEqual(raster.dataset_mask()[0, 0], 0)

    def test_construye_un_dem_por_chunk_rgb_y_ms(self):
        resultados = {}
        exportaciones = []

        def crear_chunk(etiqueta):
            dem_completo = SimpleNamespace(key=f"{etiqueta}-completo", label="")
            chunk = SimpleNamespace(
                cameras=[object()], label=etiqueta, elevation=None, elevations=[],
            )

            def construir_dem(**opciones):
                chunk.elevations.append(dem_completo)
                chunk.elevation = dem_completo
                resultados.setdefault(etiqueta, []).append(opciones)

            chunk.buildDem = construir_dem
            return chunk

        chunks = [crear_chunk("RGB"), crear_chunk("MS")]
        app = ProcesamientoMetashape(camera_model="mavic_3m")
        app._metashape = SimpleNamespace(
            DataSource=SimpleNamespace(TiePointsData="puntos_de_paso"),
        )
        app._obtener_chunks = lambda: chunks
        app._exportar_dem = lambda *args: exportaciones.append(args)
        app._guardar_documento = lambda *_: None

        app.construir_mde()

        for chunk in chunks:
            self.assertEqual(len(chunk.elevations), 1)
            self.assertEqual(chunk.elevation.label, "DEM - Puntos de paso")
            self.assertEqual(resultados[chunk.label], [{"source_data": "puntos_de_paso", "resolution": 0.5}])
        self.assertEqual(len(exportaciones), 2)

    def test_mavic_3m_rgb_y_ms_construyen_ortomosaico_con_dem_propio(self):
        llamadas_rgb = []
        llamadas_ms = []
        dem_ms = SimpleNamespace(label="DEM - Todos los puntos")
        dem_rgb = SimpleNamespace(label="DEM - Puntos de paso")
        rgb = SimpleNamespace(cameras=[object()], label="RGB", elevation=dem_rgb,
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
        self.assertIs(rgb.elevation, dem_rgb)
        self.assertEqual(llamadas_rgb, [{"surface_data": "elevacion", "resolution": 0}])
        self.assertEqual(llamadas_ms, [{"surface_data": "elevacion", "resolution": 0.2}])

    def test_mavic_rgb_construye_orto_a_resolucion_nativa(self):
        llamadas = []
        rgb = SimpleNamespace(
            cameras=[object()], label="RGB", elevation=object(),
            buildOrthomosaic=lambda **kwargs: llamadas.append(kwargs),
        )
        app = ProcesamientoMetashape(camera_model="mavic_3_rgb")
        app._metashape = SimpleNamespace(DataSource=SimpleNamespace(ElevationData="elevacion"))
        app._obtener_chunks = lambda: [rgb]
        app._guardar_documento = lambda *_: None

        app.construir_ortomosaico()

        self.assertEqual(llamadas, [{"surface_data": "elevacion", "resolution": 0}])

    def test_rededge_sin_fotos_rgb_usa_su_dem_ms(self):
        ortomosaicos = []
        dem_ms = SimpleNamespace(label="DEM - Puntos de paso")
        ms = SimpleNamespace(
            cameras=[object()], label="MS", elevation=dem_ms,
            importRaster=lambda **kwargs: self.fail("RedEdge-M no debe importar un DEM RGB"),
            buildOrthomosaic=lambda **kwargs: ortomosaicos.append(kwargs),
        )
        app = ProcesamientoMetashape(camera_model="rededge_m")
        app._metashape = SimpleNamespace(
            DataSource=SimpleNamespace(ElevationData="elevacion", ModelData="modelo"),
        )
        app._obtener_chunks = lambda: [ms]
        app._guardar_documento = lambda *_: None

        app.construir_ortomosaico()

        self.assertIs(ms.elevation, dem_ms)
        self.assertEqual(ortomosaicos, [{"surface_data": "elevacion", "resolution": 0.2}])

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
        self.assertEqual(exportaciones[0]["resolution"], 0.2)
        self.assertEqual(exportaciones[0]["path"], salida + ".metashape_raw.tif")
        self.assertEqual(verificaciones[0][0], (salida + ".metashape_raw.tif", 32614))
        self.assertEqual(verificaciones[0][1]["radiometric_correction"], "sun_sensor")
        self.assertEqual(verificaciones[0][1]["destino"], salida)

    def test_rededge_calibra_y_conserva_el_orden_de_sus_cinco_bandas(self):
        calibraciones = []
        sensor = SimpleNamespace(normalize_sensitivity=False)
        chunk = SimpleNamespace(
            sensors=[sensor],
            calibrateReflectance=lambda **opciones: calibraciones.append(opciones),
            raster_transform=SimpleNamespace(formula=[], enabled=False),
        )
        app = ProcesamientoMetashape(camera_model="rededge_m")
        app._metashape = SimpleNamespace(
            RasterTransformType=SimpleNamespace(RasterTransformValue="valores"),
            ImageFormat=SimpleNamespace(ImageFormatTIFF="tiff"),
        )
        app._proyeccion_utm_ms = lambda _: ("utm", 32613)
        app._guardar_documento = lambda *_: None

        with patch.object(app, "_clasificar_dataset", return_value=([], ["foto.tif"])):
            documento = SimpleNamespace(chunks=[], addChunk=lambda: chunk)
            app._abrir_documento = lambda crear=False: documento
            chunk.addPhotos = lambda fotos: None
            app.cargar_fotos()

        opciones, epsg = app._opciones_exportacion_ms(chunk)
        self.assertTrue(sensor.normalize_sensitivity)
        self.assertEqual(calibraciones, [{"use_reflectance_panels": False, "use_sun_sensor": True}])
        self.assertEqual(chunk.raster_transform.formula,
                         ["B1 / 32768", "B2 / 32768", "B3 / 32768", "B4 / 32768", "B5 / 32768"])
        self.assertTrue(chunk.raster_transform.enabled)
        self.assertEqual(opciones, {
            "raster_transform": "valores", "image_format": "tiff",
            "projection": "utm", "nodata_value": -10000,
        })
        self.assertEqual(epsg, 32613)

        mavic = ProcesamientoMetashape(camera_model="mavic_3m")
        mavic._metashape = app._metashape
        mavic._proyeccion_utm_ms = app._proyeccion_utm_ms
        chunk.raster_transform.formula = []
        mavic._opciones_exportacion_ms(chunk)
        self.assertEqual(chunk.raster_transform.formula,
                         ["B1 / 32768", "B2 / 32768", "B3 / 32768", "B4 / 32768"])

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

    def test_rededge_publica_cinco_bandas_en_orden_y_alpha(self):
        app = ProcesamientoMetashape(camera_model="rededge_m")
        with tempfile.TemporaryDirectory() as carpeta:
            destino = os.path.join(carpeta, "rededge_ms.tif")
            fuente = destino + ".metashape_raw.tif"
            datos = np.ones((6, 64, 64), dtype=np.float32)
            for indice in range(5):
                datos[indice] *= (indice + 1) / 10
            datos[5] *= 255
            datos[5, :4, :] = 0
            with rasterio.open(
                fuente, "w", driver="GTiff", width=64, height=64,
                count=6, dtype="float32", crs="EPSG:32613",
                transform=from_origin(0, 10, 0.2, 0.2),
            ) as dst:
                dst.write(datos)

            app._verificar_exportacion_ms(fuente, 32613, radiometric_correction="sun_sensor", destino=destino)

            with rasterio.open(destino) as src:
                self.assertEqual(src.count, 6)
                self.assertEqual(src.descriptions, ("Blue", "Green", "Red", "Red edge", "NIR", "Alpha"))
                self.assertEqual(src.crs.to_epsg(), 32613)
                self.assertEqual(src.nodata, -10000)
                self.assertEqual(src.tags()["RadiometricCorrection"], "sun_sensor")
                self.assertEqual(src.tags()["SensorModel"], "rededge_m")
                self.assertEqual([src.tags(i)["CentralWavelength"] for i in range(1, 6)],
                                 ["475", "560", "668", "717", "840"])
                self.assertEqual(src.read(5)[32, 32], 0.5)
                self.assertEqual(src.dataset_mask()[0, 0], 0)
                self.assertEqual(src.dataset_mask()[32, 32], 255)
                self.assertEqual(_mapear_bandas_rgb(src), (3, 2, 1))


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
        self.assertEqual(exportaciones[0]["resolution"], 0)
        self.assertTrue(exportaciones[0]["save_alpha"])

    def test_rededge_recorta_el_tiff_sin_perder_bandas(self):
        for formato in ("roi", "shp"):
            with self.subTest(formato=formato), tempfile.TemporaryDirectory() as carpeta:
                exportaciones = []
                importaciones = []
                chunk = SimpleNamespace(
                    cameras=[object()], label="MS",
                    importShapes=lambda **kwargs: importaciones.append(kwargs),
                    exportRaster=lambda **kwargs: exportaciones.append(kwargs),
                )
                app = ProcesamientoMetashape(camera_model="rededge_m")
                app._metashape = SimpleNamespace(
                    DataSource=SimpleNamespace(OrthomosaicData="ortomosaico"),
                )
                app._obtener_chunks = lambda: [chunk]
                app._guardar_documento = lambda *_: None
                app._opciones_exportacion_ms = lambda _: ({"raster_transform": "valores"}, 32613)
                verificaciones = []

                def verificar(*args, **kwargs):
                    verificaciones.append((args, kwargs))
                    with rasterio.open(
                        kwargs["destino"], "w", driver="GTiff", width=10, height=4,
                        count=6, dtype="float32", crs="EPSG:4326",
                        transform=from_origin(0, 4, 1, 1), nodata=-10000,
                    ) as raster:
                        datos = np.zeros((6, 4, 10), dtype="float32")
                        for banda in range(5):
                            datos[banda] = (banda + 1) / 10
                        datos[5] = 255
                        raster.write(datos)
                        raster.colorinterp = (
                            rasterio.enums.ColorInterp.blue,
                            rasterio.enums.ColorInterp.green,
                            rasterio.enums.ColorInterp.red,
                            rasterio.enums.ColorInterp.undefined,
                            rasterio.enums.ColorInterp.undefined,
                            rasterio.enums.ColorInterp.alpha,
                        )
                        raster.update_tags(BandOrder="Blue,Green,Red,RedEdge,NIR,Alpha")
                        for indice, nombre in enumerate(("Blue", "Green", "Red", "Red edge", "NIR", "Alpha"), 1):
                            raster.set_band_description(indice, nombre)

                app._verificar_exportacion_ms = verificar
                roi = os.path.join(carpeta, "recorte.geojson")
                salida = os.path.join(carpeta, "micasense_ms.tif")
                geometrias = [
                    {"type": "Polygon", "coordinates": [[
                        [1, 0], [3, 0], [3, 3], [1, 3], [1, 0],
                    ]]},
                ]
                if formato == "shp":
                    geometrias.append({"type": "Polygon", "coordinates": [[
                        [7, 1], [9, 1], [9, 4], [7, 4], [7, 1],
                    ]]})
                with open(roi, "w", encoding="utf-8") as archivo:
                    json.dump({
                        "type": "FeatureCollection",
                        "features": [{"type": "Feature", "geometry": geometria}
                                     for geometria in geometrias],
                        "_recorte": {"formato": formato},
                    }, archivo)
                with patch("backend.workflow.CLIP_GEOJSON", roi), \
                     patch("backend.workflow.RUTA_ORTOMOSAICO_MS", salida), \
                     patch("backend.workflow.RUTA_PROYECTO", os.path.join(carpeta, "proyecto.psx")):
                    app.exportar_resultado()

                self.assertEqual(len(exportaciones), 1)
                self.assertTrue(exportaciones[0]["path"].endswith(".sin_recorte.tif.metashape_raw.tif"))
                self.assertFalse(exportaciones[0]["clip_to_boundary"])
                self.assertTrue(exportaciones[0]["save_alpha"])
                self.assertEqual(exportaciones[0]["raster_transform"], "valores")
                self.assertEqual(verificaciones[0][0], (exportaciones[0]["path"], 32613))
                self.assertNotEqual(verificaciones[0][1]["destino"], salida)
                self.assertEqual(importaciones, [])
                self.assertFalse(os.path.exists(verificaciones[0][1]["destino"]))
                with rasterio.open(salida) as raster:
                    self.assertEqual(raster.count, 6)
                    self.assertEqual(raster.descriptions, ("Blue", "Green", "Red", "Red edge", "NIR", "Alpha"))
                    self.assertEqual(raster.tags()["BandOrder"], "Blue,Green,Red,RedEdge,NIR,Alpha")
                    self.assertEqual(raster.read(5)[1, 1], 0.5)
                    self.assertEqual(raster.read(6)[1, 1], 255)
                    if formato == "roi":
                        self.assertEqual((raster.width, raster.height), (2, 3))
                    else:
                        self.assertEqual(raster.dataset_mask()[0, 0], 0)
                if formato == "shp":
                    for indice in (1, 2):
                        pieza = os.path.join(carpeta, f"micasense_ms_poligono_{indice:03d}.tif")
                        with rasterio.open(pieza) as raster:
                            self.assertEqual(raster.count, 6)
                            self.assertEqual((raster.width, raster.height), (2, 3))


if __name__ == "__main__":
    unittest.main()
