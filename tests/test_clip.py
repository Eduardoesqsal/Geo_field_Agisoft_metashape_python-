import json
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

from backend.services.clip import recortar_ortomosaico


class RecorteOrtomosaicoTest(unittest.TestCase):
    def test_recorta_a_otro_archivo_si_el_original_sigue_abierto(self):
        with tempfile.TemporaryDirectory() as carpeta:
            raster = os.path.join(carpeta, "ortomosaico.tif")
            salida = os.path.join(carpeta, "ortomosaico_recortado.tif")
            limite = os.path.join(carpeta, "recorte.geojson")
            with rasterio.open(
                raster, "w", driver="GTiff", width=4, height=4, count=3,
                dtype="uint8", crs="EPSG:4326", transform=from_origin(0, 4, 1, 1),
            ) as destino:
                destino.write(np.full((3, 4, 4), 100, dtype="uint8"))
            with open(limite, "w", encoding="utf-8") as archivo:
                json.dump({"features": [{"geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]],
                }}]}, archivo)
            with rasterio.open(raster) as origen_abierto:
                self.assertTrue(recortar_ortomosaico(raster, limite, salida=salida))
                self.assertEqual((origen_abierto.width, origen_abierto.height), (4, 4))
            with rasterio.open(salida) as resultado:
                self.assertEqual((resultado.width, resultado.height), (2, 2))

    def test_dos_poligonos_generan_dos_tiff_independientes(self):
        with tempfile.TemporaryDirectory() as carpeta:
            raster = os.path.join(carpeta, "ortomosaico.tif")
            limite = os.path.join(carpeta, "recorte.geojson")
            with rasterio.open(
                raster, "w", driver="GTiff", width=10, height=4, count=3,
                dtype="uint8", crs="EPSG:4326", transform=from_origin(0, 4, 1, 1),
            ) as destino:
                destino.write(np.full((3, 4, 10), 100, dtype="uint8"))
            poligonos = [
                {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]},
                {"type": "Polygon", "coordinates": [[[8, 2], [10, 2], [10, 4], [8, 4], [8, 2]]]},
            ]
            with open(limite, "w", encoding="utf-8") as archivo:
                json.dump({"features": [{"geometry": poligono} for poligono in poligonos]}, archivo)

            salidas = [os.path.join(carpeta, f"parte_{indice}.tif") for indice in range(2)]
            for indice, salida in enumerate(salidas):
                self.assertTrue(recortar_ortomosaico(raster, limite, salida=salida, indice=indice))
            self.assertTrue(recortar_ortomosaico(raster, limite))

            with rasterio.open(salidas[0]) as primera, rasterio.open(salidas[1]) as segunda:
                self.assertEqual((primera.width, primera.height), (2, 2))
                self.assertEqual((segunda.width, segunda.height), (2, 2))
                self.assertNotEqual(primera.bounds, segunda.bounds)
                self.assertTrue(np.all(primera.dataset_mask() == 255))
                self.assertTrue(np.all(segunda.dataset_mask() == 255))
            with rasterio.open(raster) as conjunto:
                self.assertEqual((conjunto.width, conjunto.height), (10, 4))
                self.assertEqual(conjunto.dataset_mask()[0, 0], 0)
                self.assertEqual(conjunto.dataset_mask()[0, 9], 255)

    def test_reintenta_reemplazo_si_windows_ocupa_el_tiff(self):
        with tempfile.TemporaryDirectory() as carpeta:
            raster = os.path.join(carpeta, "ortomosaico.tif")
            limite = os.path.join(carpeta, "recorte.geojson")
            with rasterio.open(
                raster, "w", driver="GTiff", width=4, height=4, count=4,
                dtype="uint8", crs="EPSG:4326", transform=from_origin(0, 4, 1, 1),
            ) as destino:
                destino.write(np.full((4, 4, 4), 100, dtype="uint8"))
            poligono = {"type": "Polygon", "coordinates": [[[1, 1], [3, 1], [1, 3], [1, 1]]]}
            with open(limite, "w", encoding="utf-8") as archivo:
                json.dump({"features": [{"geometry": poligono}]}, archivo)
            reemplazar = os.replace
            intentos = []

            def reemplazo_temporal(origen, destino):
                intentos.append(1)
                if len(intentos) == 1:
                    raise PermissionError("archivo ocupado")
                reemplazar(origen, destino)

            with patch("backend.services.clip.os.replace", side_effect=reemplazo_temporal), \
                 patch("backend.services.clip.time.sleep"):
                self.assertTrue(recortar_ortomosaico(raster, limite))
            self.assertEqual(len(intentos), 2)
            with rasterio.open(raster) as resultado:
                self.assertEqual((resultado.width, resultado.height), (2, 2))

    def test_recorta_extension_y_transparencia(self):
        with tempfile.TemporaryDirectory() as carpeta:
            raster = os.path.join(carpeta, "ortomosaico.tif")
            limite = os.path.join(carpeta, "recorte.geojson")
            with rasterio.open(
                raster, "w", driver="GTiff", width=10, height=10, count=4,
                dtype="uint8", crs="EPSG:4326", transform=from_origin(0, 10, 1, 1),
            ) as destino:
                destino.write(np.full((4, 10, 10), 100, dtype="uint8"))
                destino.write(np.full((10, 10), 255, dtype="uint8"), 4)
            # El cuadro delimitador es de 6x6; el triangulo deja pixeles transparentes.
            poligono = {"type": "Polygon", "coordinates": [[[2, 2], [8, 2], [2, 8], [2, 2]]]}
            with open(limite, "w", encoding="utf-8") as archivo:
                json.dump({"type": "FeatureCollection", "features": [
                    {"type": "Feature", "properties": {}, "geometry": poligono}
                ]}, archivo)

            self.assertTrue(recortar_ortomosaico(raster, limite))
            with rasterio.open(raster) as resultado:
                self.assertEqual((resultado.width, resultado.height), (6, 6))
                self.assertEqual(resultado.count, 4)
                self.assertTrue(np.any(resultado.dataset_mask() == 0))
                self.assertTrue(np.any(resultado.dataset_mask() == 255))
                self.assertTrue(np.all(resultado.read(4)[resultado.dataset_mask() == 0] == 0))

            os.remove(limite)
            self.assertFalse(recortar_ortomosaico(raster, limite))

    def test_limite_sin_interseccion_conserva_original(self):
        with tempfile.TemporaryDirectory() as carpeta:
            raster = os.path.join(carpeta, "ortomosaico.tif")
            limite = os.path.join(carpeta, "recorte.geojson")
            with rasterio.open(
                raster, "w", driver="GTiff", width=4, height=4, count=4,
                dtype="uint8", crs="EPSG:4326", transform=from_origin(0, 4, 1, 1),
            ) as destino:
                destino.write(np.full((4, 4, 4), 255, dtype="uint8"))
            poligono = {"type": "Polygon", "coordinates": [[[20, 20], [21, 20], [21, 21], [20, 20]]]}
            with open(limite, "w", encoding="utf-8") as archivo:
                json.dump({"type": "FeatureCollection", "features": [
                    {"type": "Feature", "properties": {}, "geometry": poligono}
                ]}, archivo)
            with self.assertRaisesRegex(RuntimeError, "no cruza"):
                recortar_ortomosaico(raster, limite)
            with rasterio.open(raster) as original:
                self.assertEqual((original.width, original.height), (4, 4))


if __name__ == "__main__":
    unittest.main()
