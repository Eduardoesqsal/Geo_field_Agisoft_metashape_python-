"""Recorte opcional de ortomosaicos con un ROI guardado en WGS84."""

import json
import os
import time

import numpy as np
import rasterio
from rasterio.errors import WindowError
from rasterio.features import geometry_mask, geometry_window
from rasterio.warp import transform_geom
from rasterio.windows import Window, transform as window_transform

from backend.core.paths import CLIP_GEOJSON


def recortar_ortomosaico(ruta, limite=CLIP_GEOJSON):
    if not os.path.isfile(limite):
        return False

    with open(limite, encoding="utf-8") as archivo:
        contenido = json.load(archivo)
    geometrias = [
        feature["geometry"] for feature in contenido.get("features", [])
        if feature.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}
    ]
    if not geometrias:
        raise RuntimeError("El ROI de recorte no contiene poligonos")

    temporal = ruta + ".recorte.tmp.tif"
    try:
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(ruta) as origen:
            if origen.crs is None:
                raise RuntimeError(f"El ortomosaico no tiene CRS: {ruta}")
            geometrias_raster = [
                transform_geom("EPSG:4326", origen.crs, geometria)
                for geometria in geometrias
            ]
            try:
                ventana = geometry_window(origen, geometrias_raster)
            except WindowError as exc:
                raise RuntimeError("El ROI de recorte no cruza el ortomosaico") from exc
            perfil = origen.profile.copy()
            perfil.update(
                width=int(ventana.width),
                height=int(ventana.height),
                transform=window_transform(ventana, origen.transform),
                compress="deflate",
                BIGTIFF="IF_SAFER",
            )
            # Algunos TIFF de origen usan bloques mayores que el recorte.
            perfil.pop("blockxsize", None)
            perfil.pop("blockysize", None)
            perfil["tiled"] = True
            tiene_datos = False
            with rasterio.open(temporal, "w", **perfil) as destino:
                destino.colorinterp = origen.colorinterp
                destino.update_tags(**origen.tags())
                for indice in origen.indexes:
                    destino.set_band_description(indice, origen.descriptions[indice - 1])
                    destino.update_tags(indice, **origen.tags(indice))
                for _, salida in destino.block_windows(1):
                    entrada = Window(
                        salida.col_off + ventana.col_off,
                        salida.row_off + ventana.row_off,
                        salida.width,
                        salida.height,
                    )
                    dentro = geometry_mask(
                        geometrias_raster,
                        out_shape=(int(salida.height), int(salida.width)),
                        transform=window_transform(entrada, origen.transform),
                        invert=True,
                    )
                    valido = dentro & (origen.dataset_mask(window=entrada) > 0)
                    tiene_datos = tiene_datos or bool(np.any(valido))
                    datos = origen.read(window=entrada)
                    relleno = origen.nodata if origen.nodata is not None else 0
                    datos[:, ~valido] = relleno
                    # Solo la banda Alpha explicita es transparencia. En el
                    # ortomosaico Mavic 3M de cuatro bandas, la ultima es NIR.
                    if (origen.colorinterp[-1] == rasterio.enums.ColorInterp.alpha
                            or (origen.descriptions[-1] or "").strip().lower() == "alpha"):
                        datos[-1, ~valido] = 0
                    destino.write(datos, window=salida)
                    destino.write_mask(np.where(valido, 255, 0).astype("uint8"), window=salida)
            if not tiene_datos:
                raise RuntimeError("El ROI de recorte no cubre pixeles validos del ortomosaico")
        for intento in range(5):
            try:
                os.replace(temporal, ruta)
                break
            except PermissionError:
                if intento == 4:
                    raise
                time.sleep(1)
    finally:
        if os.path.exists(temporal):
            os.remove(temporal)
    return True
