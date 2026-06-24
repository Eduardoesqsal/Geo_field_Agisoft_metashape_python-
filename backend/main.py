from datetime import datetime
import os
import pathlib
import sys
import traceback

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.workflow import ProcesamientoMetashape


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


def crear_logger():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_name = datetime.now().strftime("metashape_%Y%m%d_%H%M%S.log")
    log_path = os.path.join(log_dir, log_name)
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = Tee(original_stdout, log_file)
    sys.stderr = Tee(original_stderr, log_file)
    print(f"[LOG] Guardando salida en: {log_path}", flush=True)
    return log_file, original_stdout, original_stderr


def main():
    log_file, original_stdout, original_stderr = crear_logger()
    print("--- INICIANDO FLUJO COMPLETO ---", flush=True)
    app = None

    try:
        camera_model = os.environ.get("METASHAPE_CAMERA_MODEL", "mavic_3m")
        app = ProcesamientoMetashape(camera_model=camera_model)
        app.cargar_fotos()
        app.alinear_camaras()
        app.construir_profundidad()
        app.construir_modelo()
        app.construir_ortomosaico()
        app.exportar_resultado()

        print("\n--- FINALIZADO SIN ERRORES ---", flush=True)
    except Exception as e:
        print(f"\nERROR EN EL PROCESO: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
    finally:
        if app is not None:
            try:
                app.limpiar_temporales()
            except Exception:
                pass
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()


if __name__ == "__main__":
    main()
