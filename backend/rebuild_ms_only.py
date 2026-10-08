"""Calibra MS y reconstruye solo su ortomosaico desde un proyecto existente.

No guarda el proyecto original ni repite alineacion, nube de puntos o DEM.
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
    resolucion = float(sys.argv[3]) if len(sys.argv) == 4 else 0.25
    if not proyecto.is_file() or proyecto.suffix.lower() not in (".psx", ".psz"):
        raise RuntimeError(f"No se encontro el proyecto: {proyecto}")
    if salida.exists():
        raise RuntimeError(f"La salida ya existe: {salida}")
    if resolucion <= 0:
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

    for sensor in chunk.sensors:
        sensor.normalize_sensitivity = True
    print("Calibrando las fotos MS con el sensor solar", flush=True)
    chunk.calibrateReflectance(use_reflectance_panels=False, use_sun_sensor=True)

    elevacion = app._enum("DataSource.ElevationData", "ElevationData")
    promedio = app._enum("BlendingMode.AverageBlending", "AverageBlending")
    if elevacion is None or promedio is None:
        raise RuntimeError("Esta version de Metashape no permite construir el ortomosaico requerido")
    proyeccion, epsg = app._proyeccion_utm_ms(chunk)
    claves_anteriores = {item.key for item in chunk.orthomosaics}
    print(f"Reconstruyendo solo ortomosaico MS a {resolucion:g} m/pixel", flush=True)
    chunk.buildOrthomosaic(
        surface_data=elevacion,
        blending_mode=promedio,
        fill_holes=True,
        projection=proyeccion,
        resolution=resolucion,
        replace_asset=False,
    )
    nuevos = [item for item in chunk.orthomosaics if item.key not in claves_anteriores]
    if not nuevos:
        raise RuntimeError("Metashape no creo un ortomosaico nuevo")

    opciones, _ = app._opciones_exportacion_ms(chunk)
    fuente = app._enum("DataSource.OrthomosaicData", "OrthomosaicData")
    if fuente is None:
        raise RuntimeError("Metashape no reconoce OrthomosaicData")
    salida.parent.mkdir(parents=True, exist_ok=True)
    print(f"Exportando: {salida}", flush=True)
    chunk.exportRaster(
        path=str(salida),
        source_data=fuente,
        asset=nuevos[-1].key,
        resolution=resolucion,
        save_alpha=True,
        white_background=False,
        **opciones,
    )
    app._verificar_exportacion_ms(str(salida), epsg, radiometric_correction="sun_sensor")
    print(f"Terminado sin guardar cambios en {proyecto}", flush=True)


if __name__ == "__main__":
    main()
