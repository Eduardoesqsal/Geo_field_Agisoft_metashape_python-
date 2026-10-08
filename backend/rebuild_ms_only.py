"""Calibra MS y reconstruye su ortomosaico con el DEM propio.

Usa la misma construccion de ortomosaico que RGB. No guarda el proyecto
original ni repite alineacion, nube de puntos o DEM.
Uso: metashape.exe -r backend/rebuild_ms_only.py proyecto.psx salida.tif [resolucion_m]
"""

import pathlib
import sys

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.workflow import ProcesamientoMetashape


def main():
    if len(sys.argv) not in (3, 4):
        raise SystemExit(
            "Uso: metashape.exe -r backend/rebuild_ms_only.py "
            "<proyecto.psx> <salida.tif> [resolucion_m]"
        )

    proyecto = pathlib.Path(sys.argv[1]).resolve()
    salida = pathlib.Path(sys.argv[2]).resolve()
    resolucion = float(sys.argv[3]) if len(sys.argv) == 4 else None
    if not proyecto.is_file() or proyecto.suffix.lower() not in (".psx", ".psz"):
        raise RuntimeError(f"No se encontro el proyecto: {proyecto}")
    if salida.exists():
        raise RuntimeError(f"La salida ya existe: {salida}")
    if resolucion is not None and resolucion <= 0:
        raise ValueError("La resolucion debe ser positiva y estar en metros")

    app = ProcesamientoMetashape(camera_model="mavic_3m")
    Metashape = app._ms()
    doc = Metashape.Document()
    doc.open(str(proyecto))
    chunk = next(
        (item for item in doc.chunks
         if (getattr(item, "label", "") or "").strip().lower() == "ms"),
        None,
    )
    if chunk is None or getattr(chunk, "elevation", None) is None:
        raise RuntimeError("El proyecto necesita un chunk MS con DEM existente")
    dem_ms = next(
        (item for item in chunk.elevations
         if (getattr(item, "label", "") or "").strip() == "DEM - Suelo clasificado (Ground)"),
        None,
    )
    if dem_ms is None:
        raise RuntimeError("El chunk MS no contiene su DEM de suelo clasificado")

    for sensor in chunk.sensors:
        sensor.normalize_sensitivity = True
    print("Calibrando las fotos MS con el sensor solar", flush=True)
    chunk.calibrateReflectance(use_reflectance_panels=False, use_sun_sensor=True)

    elevacion = app._enum("DataSource.ElevationData", "ElevationData")
    if elevacion is None:
        raise RuntimeError("Esta version de Metashape no permite construir el ortomosaico requerido")
    claves_anteriores = {item.key for item in chunk.orthomosaics}
    print("Reconstruyendo ortomosaico MS con su DEM de suelo", flush=True)
    dem_anterior = chunk.elevation
    chunk.elevation = dem_ms
    try:
        chunk.buildOrthomosaic(surface_data=elevacion, replace_asset=False)
    finally:
        chunk.elevation = dem_anterior
    nuevos = [item for item in chunk.orthomosaics if item.key not in claves_anteriores]
    if not nuevos:
        raise RuntimeError("Metashape no creo un ortomosaico nuevo")

    opciones, epsg = app._opciones_exportacion_ms(chunk)
    fuente = app._enum("DataSource.OrthomosaicData", "OrthomosaicData")
    if fuente is None:
        raise RuntimeError("Metashape no reconoce OrthomosaicData")
    salida.parent.mkdir(parents=True, exist_ok=True)
    print(f"Exportando: {salida}", flush=True)
    fuente_ms = str(salida) + ".metashape_raw.tif"
    chunk.exportRaster(
        path=fuente_ms,
        source_data=fuente,
        asset=nuevos[-1].key,
        save_alpha=True,
        white_background=False,
        **({"resolution": resolucion} if resolucion is not None else {}),
        **opciones,
    )
    app._verificar_exportacion_ms(
        fuente_ms, epsg, radiometric_correction="sun_sensor",
        expected_resolution=resolucion, destino=str(salida)
    )
    print(f"Terminado sin guardar cambios en {proyecto}", flush=True)


if __name__ == "__main__":
    main()
