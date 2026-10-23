import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import ColorInterp
from rasterio.transform import from_bounds

from backend.services.overlay import (
    _bounds_ortomosaico_rgb,
    _cache_buster_tiles_rgb,
    _generar_png_overlay_rgb,
    _leer_tile_ortomosaico,
)


class OverlayRecortadoTest(unittest.TestCase):
    def test_mapa_oculta_ortomosaico_fuera_del_shapefile(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "ortomosaico_rgb.tif")
            limite = os.path.join(carpeta, "recorte.geojson")
            norte = 66.513260443
            with rasterio.open(
                ruta, "w", driver="GTiff", width=4, height=4, count=4,
                dtype="uint8", crs="EPSG:4326",
                transform=from_bounds(-90, 0, 0, norte, 4, 4),
            ) as destino:
                destino.write(np.full((4, 4, 4), 100, dtype="uint8"))
                destino.write(np.full((4, 4), 255, dtype="uint8"), 4)
                destino.colorinterp = (
                    ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha,
                )
            with patch("backend.services.overlay.RUTA_ORTOMOSAICO_RGB", ruta), \
                 patch("backend.services.overlay.CLIP_GEOJSON", limite):
                version_sin_recorte = _cache_buster_tiles_rgb(ruta)
                with open(limite, "w", encoding="utf-8") as archivo:
                    json.dump({"features": [{"geometry": {
                        "type": "Polygon", "coordinates": [[
                            [-90, 0], [-45, 0], [-45, norte], [-90, norte], [-90, 0],
                        ]],
                    }}]}, archivo)
                self.assertNotEqual(version_sin_recorte, _cache_buster_tiles_rgb(ruta))
                bounds = _bounds_ortomosaico_rgb()
                tile = np.asarray(Image.open(io.BytesIO(_leer_tile_ortomosaico(2, 1, 1))))
                previo = np.asarray(Image.open(io.BytesIO(_generar_png_overlay_rgb())))

            self.assertEqual(tile[128, 64, 3], 255)
            self.assertEqual(tile[128, 192, 3], 0)
            self.assertAlmostEqual(bounds[1][1], -45)
            self.assertEqual(previo[2, 0, 3], 255)
            self.assertEqual(previo[2, 3, 3], 0)


if __name__ == "__main__":
    unittest.main()
