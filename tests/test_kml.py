import asyncio
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import geopandas as gpd
from fastapi import UploadFile
from fastapi import HTTPException
from shapely.geometry import Polygon

from backend.services.vector_overlay import _cargar_overlay_vectorial_desde_ruta, cargar_y_sincronizar_desde_upload, guardar_roi_dibujado, recuperar_recorte_guardado
import backend.runtime as runtime


class ImportarKMLTest(unittest.TestCase):
    def test_shapefile_sin_dbf_devuelve_error_claro(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "incompleto.shp")
            with open(ruta, "wb") as archivo:
                archivo.write(b"shapefile incompleto")
            with self.assertRaises(HTTPException) as error:
                _cargar_overlay_vectorial_desde_ruta(ruta)
            self.assertEqual(error.exception.status_code, 400)
            self.assertIn(".dbf", error.exception.detail)

    def test_shapefile_con_dos_poligonos_activa_recorte(self):
        with tempfile.TemporaryDirectory() as carpeta:
            shapefile = os.path.join(carpeta, "areas.shp")
            gpd.GeoDataFrame(
                {"nombre": ["uno", "dos"]},
                geometry=[
                    Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                    Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
                ],
                crs="EPSG:4326",
            ).to_file(shapefile)
            archivo_zip = os.path.join(carpeta, "areas.zip")
            with zipfile.ZipFile(archivo_zip, "w") as comprimido:
                for nombre in os.listdir(carpeta):
                    if nombre.startswith("areas.") and nombre not in {"areas.zip", "areas.shx"}:
                        comprimido.write(os.path.join(carpeta, nombre), nombre)
            limite = os.path.join(carpeta, "recorte.geojson")
            with open(archivo_zip, "rb") as archivo:
                upload = UploadFile(file=io.BytesIO(archivo.read()), filename="areas.zip")
            with patch("backend.services.vector_overlay.CLIP_GEOJSON", limite):
                respuesta = asyncio.run(cargar_y_sincronizar_desde_upload(upload))
            self.assertEqual(respuesta["feature_count"], 2)
            with open(limite, encoding="utf-8") as archivo:
                recorte = json.load(archivo)
            self.assertEqual(recorte["_recorte"]["formato"], "shp")
            self.assertEqual(len(recorte["features"]), 2)

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
