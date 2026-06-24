import os
import subprocess
import threading
from datetime import datetime

from fastapi import HTTPException

import backend.runtime as runtime
from backend.services.overlay import _sincronizar_overlay_proyecto


def reader_loop(proc):
    try:
        for line in proc.stdout:
            runtime.push_log(line)
            if "[1/6]" in line:
                runtime.update_state(step="cargando_fotos", message=line.strip())
            elif "[2/6]" in line:
                runtime.update_state(step="alineando_camaras", message=line.strip())
            elif "[3/6]" in line:
                runtime.update_state(step="construyendo_profundidad", message=line.strip())
            elif "[4/6]" in line:
                runtime.update_state(step="construyendo_modelo", message=line.strip())
            elif "[5/6]" in line:
                runtime.update_state(step="construyendo_ortomosaico", message=line.strip())
            elif "[6/6]" in line:
                runtime.update_state(step="exportando_resultado", message=line.strip())
            elif "FINALIZADO SIN ERRORES" in line or "PIPELINE FINALIZADO" in line:
                runtime.update_state(message=line.strip())
    finally:
        code = proc.poll()
        if code is None:
            try:
                code = proc.wait(timeout=5)
            except Exception:
                try:
                    proc.terminate()
                    code = proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    code = proc.poll()
        runtime.update_state(
            running=False,
            finished_at=datetime.now().isoformat(timespec="seconds"),
            returncode=code,
        )
        if code == 0:
            runtime.update_state(step="finalizado", message="Agisoft detenido. RGB y MS listos")
            runtime.push_log("Proceso finalizado sin errores")
            _sincronizar_overlay_proyecto()
        elif code is not None:
            runtime.update_state(step="error", message=f"Proceso terminado con codigo {code}")
            runtime.push_log(f"Proceso terminado con codigo {code}")


def iniciar_proceso(nombre_proyecto=None, camera_model=None):
    with runtime.lock:
        if runtime.state["running"]:
            raise HTTPException(status_code=409, detail="Ya hay un proceso en ejecucion")
        if not os.path.exists(runtime.METASHAPE_EXE):
            raise HTTPException(status_code=500, detail=f"No existe Metashape.exe en {runtime.METASHAPE_EXE}")
        if not os.path.exists(runtime.MAIN_SCRIPT):
            raise HTTPException(status_code=500, detail=f"No existe main.py en {runtime.MAIN_SCRIPT}")
        if runtime.ingesta_info["imagenes_validas"] <= 0:
            raise HTTPException(status_code=400, detail="Primero carga un ZIP o un enlace de Drive con imagenes validas")

        if nombre_proyecto:
            nombre_limpio = runtime.sanitizar_nombre_proyecto(nombre_proyecto)
            runtime.ingesta_info["nombre_proyecto"] = nombre_limpio
        else:
            nombre_limpio = runtime.nombre_proyecto_actual()

        modelo_camara = runtime.normalizar_modelo_camara(camera_model)
        runtime.ingesta_info["camera_model"] = modelo_camara

        runtime.logs.clear()
        runtime.state.update(
            running=True,
            step="iniciando",
            message=f"Iniciando proceso: {nombre_limpio}",
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None,
            returncode=None,
            error=None,
        )

        env = os.environ.copy()
        env["METASHAPE_PROJECT_NAME"] = nombre_limpio
        env["METASHAPE_CAMERA_MODEL"] = modelo_camara

        runtime.process = subprocess.Popen(
            [runtime.METASHAPE_EXE, "-r", runtime.MAIN_SCRIPT],
            cwd=runtime.BASE_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        runtime.reader_thread = threading.Thread(target=reader_loop, args=(runtime.process,), daemon=True)
        runtime.reader_thread.start()

    runtime.push_log("Solicitud de inicio recibida")
    return {
        "ok": True,
        "message": f"Proceso iniciado en segundo plano: {nombre_limpio}",
        "nombre_proyecto": nombre_limpio,
        "camera_model": modelo_camara,
    }


def detener_proceso():
    with runtime.lock:
        if not runtime.state["running"] or runtime.process is None:
            raise HTTPException(status_code=409, detail="No hay proceso en ejecucion")
        runtime.process.terminate()
        runtime.state["message"] = "Terminando proceso..."
    return {"ok": True, "message": "Se solicito detener el proceso"}
