"""Exporta una vista de prueba MS desde un proyecto Metashape ya procesado.

Uso desde la raiz del repositorio:
    metashape.exe -r backend/export_ms_only.py C:\ruta\proyecto.psx C:\ruta\prueba_ms.tif
"""

import pathlib
import sys

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.workflow import ProcesamientoMetashape


def main():
    argumentos = sys.argv[1:]
    if len(argumentos) not in (2, 3):
        raise SystemExit(
            "Uso: metashape.exe -r backend/export_ms_only.py "
            "<proyecto.psx> <salida.tif> [resolucion_m]"
        )

    proyecto = pathlib.Path(argumentos[0]).resolve()
    salida = pathlib.Path(argumentos[1]).resolve()
    resolucion = float(argumentos[2]) if len(argumentos) == 3 else 0.25
    if not proyecto.is_file() or proyecto.suffix.lower() not in (".psx", ".psz"):
        raise RuntimeError(f"No se encontro un proyecto Metashape valido: {proyecto}")
    if salida.exists():
        raise RuntimeError(f"La salida ya existe; elige otro nombre: {salida}")
    if resolucion <= 0:
        raise ValueError("La resolucion debe ser positiva y estar en metros")

    app = ProcesamientoMetashape(camera_model="mavic_3m")
    Metashape = app._ms()
    documento = Metashape.Document()
    documento.open(str(proyecto))
    app.doc = documento
    chunk = next(
        (item for item in documento.chunks
         if (getattr(item, "label", "") or "").strip().lower() == "ms"),
        None,
    )
    if chunk is None or getattr(chunk, "orthomosaic", None) is None:
        raise RuntimeError("El proyecto no contiene un chunk MS con ortomosaico construido")

    opciones, epsg = app._opciones_exportacion_ms(chunk)
    fuente = app._enum("DataSource.OrthomosaicData", "OrthomosaicData")
    if fuente is None:
        raise RuntimeError("Metashape no reconoce OrthomosaicData")
    salida.parent.mkdir(parents=True, exist_ok=True)
    print(f"Exportando solo MS: {salida} a {resolucion:g} m/pixel", flush=True)
    chunk.exportRaster(
        path=str(salida),
        source_data=fuente,
        resolution=resolucion,
        save_alpha=True,
        white_background=False,
        **opciones,
    )
    app._verificar_exportacion_ms(str(salida), epsg)
    print(f"Exportacion verificada: {salida}", flush=True)


if __name__ == "__main__":
    main()
