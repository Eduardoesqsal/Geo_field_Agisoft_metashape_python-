import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.workflow import ProcesamientoMetashape


class ExportacionRoiNativoTest(unittest.TestCase):
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
