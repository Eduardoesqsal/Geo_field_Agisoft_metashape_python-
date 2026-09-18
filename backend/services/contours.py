import json
import os
from datetime import datetime

import geopandas as gpd
import rasterio

import backend.runtime as runtime


def _corregir_cotas_geoidales(gdf):
    """Convierte alturas elipsoidales h a ortometricas H = h - N."""
    ruta_geoid = os.path.join(runtime.BASE_DIR, "geoids", "mx_inegi_GGM10.tif")
    if not os.path.exists(ruta_geoid):
        return gdf, False

    puntos = gdf.geometry.representative_point()
    coordenadas = [(float(p.x), float(p.y)) for p in puntos]
    with rasterio.open(ruta_geoid) as geoid:
        undulaciones = [float(valor[0]) for valor in geoid.sample(coordenadas, indexes=1)]

    def corregir(row, indice):
        propiedades = row.drop(labels=["geometry"], errors="ignore").to_dict()
        elevacion = propiedades.get("elevacion_m")
        if elevacion is None:
            elevacion = next(
            (valor for clave, valor in propiedades.items() if str(clave).lower() in {"elevation", "altitude", "cota"}),
            None,
            )
        try:
            return float(elevacion) - undulaciones[indice]
        except (TypeError, ValueError, IndexError):
            return None

    gdf = gdf.copy()
    gdf["elevacion_m"] = [corregir(row, indice) for indice, (_, row) in enumerate(gdf.iterrows())]
    gdf["datum_vertical"] = "GGM2010"
    gdf["tipo_altura"] = "ortometrica_msnm"
    return gdf, True
def _ruta_curvas_nivel():
    nombre = runtime.nombre_proyecto_actual()
    return os.path.join(runtime.BASE_DIR, "proyecto", f"{nombre}_curvas_5m.geojson")


def _sincronizar_curvas_nivel():
    ruta = _ruta_curvas_nivel()
    if not os.path.exists(ruta):
        return None

    gdf = gpd.read_file(ruta)
    if gdf.empty:
        raise RuntimeError("El GeoJSON de curvas no contiene geometria")
    gdf = gdf[~gdf.geometry.isna()]
    if gdf.crs is not None and str(gdf.crs).upper() not in {"EPSG:4326", "OGC:CRS84"}:
        gdf = gdf.to_crs(epsg=4326)
    elif gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326, allow_override=True)

    gdf, geoid_aplicado = _corregir_cotas_geoidales(gdf)

    with runtime.lock:
        intervalo_delgada = float(runtime.curvas_nivel_cache.get("intervalo_m") or 5)
        intervalo_maestra = float(runtime.curvas_nivel_cache.get("intervalo_maestra_m") or 25)

    # Metashape exporta la elevacion como atributo. Marcamos las cotas
    # maestras para que el frontend pueda resaltarlas sin duplicar lineas.
    def clasificar_curva(row):
        propiedades = row.drop(labels=["geometry"], errors="ignore").to_dict()
        elevacion = next(
            (valor for clave, valor in propiedades.items() if str(clave).lower() in {"elevation", "altitude", "cota"}),
            None,
        )
        try:
            elevacion = float(elevacion)
            es_maestra = abs(elevacion / intervalo_maestra - round(elevacion / intervalo_maestra)) < 1e-4
        except (TypeError, ValueError, ZeroDivisionError):
            es_maestra = False
        propiedades["tipo_curva"] = "maestra" if es_maestra else "delgada"
        propiedades["es_maestra"] = es_maestra
        if elevacion is not None and not geoid_aplicado:
            propiedades["elevacion_m"] = elevacion
        return propiedades

    gdf["_propiedades_curva"] = gdf.apply(clasificar_curva, axis=1)
    geojson = json.loads(gdf.drop(columns=["_propiedades_curva"]).to_json())
    for feature, propiedades in zip(geojson.get("features", []), gdf["_propiedades_curva"]):
        feature["properties"] = propiedades
    minx, miny, maxx, maxy = map(float, gdf.total_bounds)
    bounds = [[miny, minx], [maxy, maxx]]
    with runtime.lock:
        runtime.curvas_nivel_cache.update({
            "geojson": geojson,
            "bounds": bounds,
            "cache_buster": int(datetime.now().timestamp()),
            "intervalo_m": intervalo_delgada,
            "intervalo_maestra_m": intervalo_maestra,
            "feature_count": int(len(gdf)),
        })
        return dict(runtime.curvas_nivel_cache)


def estado_curvas_nivel():
    with runtime.lock:
        cache = dict(runtime.curvas_nivel_cache)
    if cache["geojson"] is None and os.path.exists(_ruta_curvas_nivel()):
        try:
            cache = _sincronizar_curvas_nivel() or cache
        except Exception:
            pass
    return {
        "ok": True,
        "disponible": cache["geojson"] is not None,
        "bounds": cache["bounds"],
        "cache_buster": cache["cache_buster"],
        "intervalo_m": cache["intervalo_m"],
        "intervalo_maestra_m": cache["intervalo_maestra_m"],
        "feature_count": cache["feature_count"],
    }


def geojson_curvas_nivel():
    with runtime.lock:
        geojson = runtime.curvas_nivel_cache.get("geojson")
    if geojson is None:
        _sincronizar_curvas_nivel()
        with runtime.lock:
            geojson = runtime.curvas_nivel_cache.get("geojson")
    return geojson


def eliminar_curvas_nivel():
    ruta = _ruta_curvas_nivel()
    with runtime.lock:
        if runtime.state.get("running"):
            raise RuntimeError("No puedes eliminar las curvas mientras hay un proceso en ejecucion")

        eliminado = False
        try:
            os.remove(ruta)
            eliminado = True
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(f"No se pudo eliminar el archivo de curvas: {exc}") from exc

        runtime.curvas_nivel_cache.clear()
        runtime.curvas_nivel_cache.update({
            "geojson": None,
            "bounds": None,
            "cache_buster": None,
            "intervalo_m": 5,
            "intervalo_maestra_m": 25,
            "feature_count": None,
        })

    runtime.push_log("Curvas de nivel eliminadas; listas para regenerar")
    return {
        "ok": True,
        "eliminado": eliminado,
        "message": "Curvas de nivel eliminadas. Ya puedes generarlas nuevamente",
    }
