import os
import subprocess
import threading
from datetime import datetime

from fastapi import HTTPException

import backend.core.state as runtime_state
from backend.core.naming import normalizar_modelo_camara, sanitizar_nombre_proyecto
from backend.core.paths import BASE_DIR, CONTOURS_SCRIPT, MAIN_SCRIPT, METASHAPE_EXE
from backend.services.overlay import _sincronizar_overlay_proyecto
from backend.services.contours import _sincronizar_curvas_nivel


def reader_loop(proc, task="pipeline"):
    try:
        for line in proc.stdout:
            runtime_state.push_log(line)
            if "[1/5]" in line:
                runtime_state.update_state(step="cargando_fotos", message=line.strip())
            elif "[2/5]" in line:
                runtime_state.update_state(step="alineando_camaras", message=line.strip())
            elif "[3/5]" in line:
                runtime_state.update_state(step="construyendo_mde", message=line.strip())
            elif "[4/5]" in line:
                runtime_state.update_state(step="construyendo_ortomosaico", message=line.strip())
            elif "[5/5]" in line:
                runtime_state.update_state(step="exportando_resultado", message=line.strip())
            elif "[7/7]" in line:
                runtime_state.update_state(step="generando_curvas", message=line.strip())
            elif "FINALIZADO SIN ERRORES" in line or "PIPELINE FINALIZADO" in line:
                runtime_state.update_state(message=line.strip())
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
        runtime_state.update_state(
            running=False,
            finished_at=datetime.now().isoformat(timespec="seconds"),
            returncode=code,
        )
        if code == 0:
            message = (
                "Curvas de nivel generadas"
                if task == "contours"
                else "Agisoft finalizado. Ortomosaicos y DEM de suelo/sin clasificar listos"
            )
            runtime_state.update_state(step="finalizado", message=message)
            runtime_state.push_log("Proceso finalizado sin errores")
            if task != "contours":
                _sincronizar_overlay_proyecto()
            try:
                _sincronizar_curvas_nivel()
            except Exception as exc:
                runtime_state.push_log(f"No se pudo sincronizar curvas: {exc}")
        elif code is not None:
            runtime_state.update_state(step="error", message=f"Proceso terminado con codigo {code}")
            runtime_state.push_log(f"Proceso terminado con codigo {code}")


def iniciar_proceso(nombre_proyecto=None, camera_model=None):
    with runtime_state.lock:
        if runtime_state.state["running"]:
            raise HTTPException(status_code=409, detail="Ya hay un proceso en ejecucion")
        if not os.path.exists(METASHAPE_EXE):
            raise HTTPException(status_code=500, detail=f"No existe Metashape.exe en {METASHAPE_EXE}")
        if not os.path.exists(MAIN_SCRIPT):
            raise HTTPException(status_code=500, detail=f"No existe main.py en {MAIN_SCRIPT}")
        if runtime_state.ingesta_info["imagenes_validas"] <= 0:
            raise HTTPException(status_code=400, detail="Primero carga un ZIP o un enlace de Drive con imagenes validas")

        if nombre_proyecto:
            nombre_limpio = sanitizar_nombre_proyecto(nombre_proyecto)
            runtime_state.ingesta_info["nombre_proyecto"] = nombre_limpio
        else:
            nombre_limpio = runtime_state.nombre_proyecto_actual()

        modelo_camara = normalizar_modelo_camara(camera_model)
        runtime_state.ingesta_info["camera_model"] = modelo_camara

        runtime_state.logs.clear()
        runtime_state.state.update(
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

        runtime_state.process = subprocess.Popen(
            [METASHAPE_EXE, "-r", MAIN_SCRIPT],
            cwd=BASE_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        runtime_state.reader_thread = threading.Thread(target=reader_loop, args=(runtime_state.process,), daemon=True)
        runtime_state.reader_thread.start()

    runtime_state.push_log("Solicitud de inicio recibida")
    return {
        "ok": True,
        "message": f"Proceso iniciado en segundo plano: {nombre_limpio}",
        "nombre_proyecto": nombre_limpio,
        "camera_model": modelo_camara,
    }


def iniciar_curvas_nivel(intervalo_m=5, intervalo_maestra_m=25):
    """Genera curvas desde el proyecto Metashape existente, sin reprocesarlo."""
    try:
        intervalo_m = float(intervalo_m)
        intervalo_maestra_m = float(intervalo_maestra_m)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Los intervalos deben ser numericos")
    if intervalo_m <= 0 or intervalo_maestra_m <= 0:
        raise HTTPException(status_code=422, detail="Los intervalos deben ser mayores que cero")
    if intervalo_maestra_m < intervalo_m or abs(intervalo_maestra_m / intervalo_m - round(intervalo_maestra_m / intervalo_m)) > 1e-6:
        raise HTTPException(status_code=422, detail="El intervalo maestro debe ser multiplo del intervalo delgado")

    with runtime_state.lock:
        if runtime_state.state["running"]:
            raise HTTPException(status_code=409, detail="Ya hay un proceso en ejecucion")
        if not os.path.exists(METASHAPE_EXE):
            raise HTTPException(status_code=500, detail=f"No existe Metashape.exe en {METASHAPE_EXE}")
        if not os.path.exists(CONTOURS_SCRIPT):
            raise HTTPException(status_code=500, detail="No existe el script de curvas de nivel")

        nombre = sanitizar_nombre_proyecto(runtime_state.ingesta_info.get("nombre_proyecto"))
        proyecto = os.path.join(BASE_DIR, "proyecto", f"{nombre}.psx")
        if not os.path.exists(proyecto):
            raise HTTPException(status_code=400, detail=f"No existe el proyecto procesado: {proyecto}")

        runtime_state.state.update(
            running=True,
            step="generando_curvas",
            message=f"Generando curvas delgadas cada {intervalo_m:g} m y maestras cada {intervalo_maestra_m:g} m",
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None,
            returncode=None,
            error=None,
        )
        runtime_state.curvas_nivel_cache["intervalo_m"] = intervalo_m
        runtime_state.curvas_nivel_cache["intervalo_maestra_m"] = intervalo_maestra_m
        env = os.environ.copy()
        env["METASHAPE_PROJECT_NAME"] = nombre
        env["METASHAPE_CONTOUR_INTERVAL"] = str(float(intervalo_m))
        env["METASHAPE_MAJOR_CONTOUR_INTERVAL"] = str(float(intervalo_maestra_m))
        runtime_state.process = subprocess.Popen(
            [METASHAPE_EXE, "-r", CONTOURS_SCRIPT],
            cwd=BASE_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        runtime_state.reader_thread = threading.Thread(
            target=reader_loop, args=(runtime_state.process, "contours"), daemon=True
        )

        runtime_state.reader_thread.start()

    runtime_state.push_log("Solicitud de generacion de curvas recibida")
    return {
        "ok": True,
        "message": f"Generacion iniciada: delgadas cada {intervalo_m:g} m, maestras cada {intervalo_maestra_m:g} m",
        "nombre_proyecto": nombre,
        "intervalo_m": intervalo_m,
        "intervalo_maestra_m": intervalo_maestra_m,
    }


def detener_proceso():
    with runtime_state.lock:
        if not runtime_state.state["running"] or runtime_state.process is None:
            raise HTTPException(status_code=409, detail="No hay proceso en ejecucion")
        runtime_state.process.terminate()
        runtime_state.state["message"] = "Terminando proceso..."
    return {"ok": True, "message": "Se solicito detener el proceso"}
