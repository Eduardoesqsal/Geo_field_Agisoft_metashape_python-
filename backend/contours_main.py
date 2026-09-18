"""Entry point de Metashape para generar curvas desde un proyecto existente."""

import os
import pathlib
import sys
import traceback

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.workflow import ProcesamientoMetashape


def main():
    intervalo = float(os.environ.get("METASHAPE_CONTOUR_INTERVAL", "5"))
    intervalo_maestra = float(os.environ.get("METASHAPE_MAJOR_CONTOUR_INTERVAL", "25"))
    app = ProcesamientoMetashape()
    try:
        app.generar_curvas_nivel(intervalo_m=intervalo, intervalo_maestra_m=intervalo_maestra)
        print("\n--- CURVAS FINALIZADAS SIN ERRORES ---", flush=True)
    except Exception as exc:
        print(f"\nERROR GENERANDO CURVAS: {exc}", flush=True)
        print(traceback.format_exc(), flush=True)
        raise
    finally:
        app.limpiar_temporales()


if __name__ == "__main__":
    main()
