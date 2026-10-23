import os
import json
import tempfile
import unittest
import warnings

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.errors import NodataShadowWarning
from rasterio.transform import from_origin

from backend.services.overlay import _leer_alpha_explicito
from backend.services.clip import recortar_ortomosaico


class LecturaAlphaTest(unittest.TestCase):
    def test_recorte_usa_alpha_aunque_exista_nodata(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "ortomosaico.tif")
            limite = os.path.join(carpeta, "recorte.geojson")
            with rasterio.open(
                ruta, "w", driver="GTiff", width=4, height=4,
                count=4, dtype="uint8", nodata=0,
                crs="EPSG:4326", transform=from_origin(0, 4, 1, 1),
            ) as destino:
                destino.write(np.full((4, 4, 4), 255, dtype="uint8"))
                destino.colorinterp = (
                    ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha,
                )
            with open(limite, "w", encoding="utf-8") as archivo:
                json.dump({"features": [{"geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]],
                }}]}, archivo)
            with warnings.catch_warnings(record=True) as capturadas:
                warnings.simplefilter("always", NodataShadowWarning)
                self.assertTrue(recortar_ortomosaico(ruta, limite))
            self.assertFalse(any(isinstance(item.message, NodataShadowWarning) for item in capturadas))

    def test_alpha_con_nodata_no_emite_advertencia(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "ortomosaico.tif")
            with rasterio.open(
                ruta, "w", driver="GTiff", width=2, height=2,
                count=4, dtype="uint8", nodata=0,
                transform=from_origin(0, 2, 1, 1),
            ) as destino:
                destino.write(np.full((4, 2, 2), 255, dtype="uint8"))
                destino.colorinterp = (
                    ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha,
                )
            with rasterio.open(ruta) as origen, warnings.catch_warnings(record=True) as capturadas:
                warnings.simplefilter("always", NodataShadowWarning)
                alpha = _leer_alpha_explicito(origen, 4)
            self.assertTrue(np.all(alpha == 255))
            self.assertFalse(any(isinstance(item.message, NodataShadowWarning) for item in capturadas))


if __name__ == "__main__":
    unittest.main()
