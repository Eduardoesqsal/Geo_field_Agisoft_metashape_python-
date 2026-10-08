import asyncio
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import UploadFile
from fastapi import HTTPException

from backend.services.vector_overlay import _cargar_overlay_vectorial_desde_ruta, cargar_y_sincronizar_desde_upload, guardar_roi_dibujado, recuperar_recorte_guardado
import backend.runtime as runtime


class ImportarKMLTest(unittest.TestCase):
    KML = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
        <Placemark><name>Recorte</name><Polygon><outerBoundaryIs><LinearRing>
        <coordinates>-100.06,20.76 -100.05,20.76 -100.05,20.77 -100.06,20.77 -100.06,20.76</coordinates>
        </LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>"""

    def test_poligono_kml_se_convierte_a_geojson(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "area.kml")
            with open(ruta, "w", encoding="utf-8") as archivo:
                archivo.write(self.KML)
            geojson, bounds, _, _, cantidad = _cargar_overlay_vectorial_desde_ruta(ruta)
            self.assertEqual(cantidad, 1)
            self.assertEqual(geojson["features"][0]["geometry"]["type"], "Polygon")
            self.assertLess(bounds[0][0], bounds[1][0])

    def test_upload_guarda_limite_para_el_procesamiento(self):
        with tempfile.TemporaryDirectory() as carpeta:
            limite = os.path.join(carpeta, "recorte.geojson")
            upload = UploadFile(file=io.BytesIO(self.KML.encode()), filename="area.kml")
            with patch("backend.services.vector_overlay.CLIP_GEOJSON", limite):
                respuesta = asyncio.run(cargar_y_sincronizar_desde_upload(upload))
            self.assertTrue(respuesta["ok"])
            with open(limite, encoding="utf-8") as archivo:
                self.assertEqual(json.load(archivo)["features"][0]["geometry"]["type"], "Polygon")

    def test_roi_dibujado_usa_el_mismo_recorte(self):
        with tempfile.TemporaryDirectory() as carpeta:
            limite = os.path.join(carpeta, "recorte.geojson")
            with patch("backend.services.vector_overlay.CLIP_GEOJSON", limite):
                respuesta = guardar_roi_dibujado([[-100.06, 20.76], [-100.05, 20.76], [-100.05, 20.77], [-100.06, 20.77]])
            self.assertEqual(respuesta["formato"], "roi")
            with open(limite, encoding="utf-8") as archivo:
                guardado = json.load(archivo)
            self.assertEqual(guardado["_recorte"]["nombre"], "ROI dibujado")
            self.assertEqual(guardado["features"][0]["geometry"]["type"], "Polygon")
            anterior = dict(runtime.shapefile_overlay_cache)
            try:
                runtime.shapefile_overlay_cache["geojson"] = None
                with patch("backend.services.vector_overlay.CLIP_GEOJSON", limite):
                    recuperar_recorte_guardado()
                self.assertEqual(runtime.shapefile_overlay_cache["nombre"], "ROI dibujado")
            finally:
                runtime.shapefile_overlay_cache.update(anterior)

    def test_roi_invalido_no_reemplaza_el_recorte(self):
        with tempfile.TemporaryDirectory() as carpeta:
            limite = os.path.join(carpeta, "recorte.geojson")
            with open(limite, "w", encoding="utf-8") as archivo:
                archivo.write("recorte existente")
            with patch("backend.services.vector_overlay.CLIP_GEOJSON", limite):
                with self.assertRaises(HTTPException):
                    guardar_roi_dibujado([[0, 0], [1, 1], [0, 1], [1, 0]])
            with open(limite, encoding="utf-8") as archivo:
                self.assertEqual(archivo.read(), "recorte existente")


if __name__ == "__main__":
    unittest.main()
