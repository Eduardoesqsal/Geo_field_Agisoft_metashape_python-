import os
import subprocess
import threading
from datetime import datetime

from fastapi import HTTPException

import backend.core.state as runtime_state
from backend.core.naming import normalizar_modelo_camara
from backend.core.catalog import image_count, read_project_manifest
from backend.core.paths import BASE_DIR, MAIN_SCRIPT, METASHAPE_EXE
from backend.services.overlay import _sincronizar_overlay_proyecto
from backend.services.projects import ensure_processing_project
from backend.services.job_receiver import _store as job_store


def reader_loop(proc, job=None):
    code = None
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
            elif "PIPELINE FINALIZADO" in line:
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
        if job:
            try:
                job_store.set_state(job["organization_id"], job["batch_id"],
                                    "done" if code == 0 else "failed",
                                    None if code == 0 else f"Metashape termino con codigo {code}")
            except Exception as exc:
                runtime_state.push_log(f"No se pudo actualizar la cola local: {exc}")
        runtime_state.update_state(
            running=False,
            finished_at=datetime.now().isoformat(timespec="seconds"),
            returncode=code,
        )
        if code == 0:
            runtime_state.update_state(step="finalizado", message="Agisoft finalizado. Ortomosaicos y DEM listos")
            runtime_state.push_log("Proceso finalizado sin errores")
            _sincronizar_overlay_proyecto()
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
        dataset_id = runtime_state.ingesta_info.get("dataset_id") or "legacy"
        if image_count(runtime_state.active_dataset_dir(), recursive=dataset_id != "legacy") <= 0:
            raise HTTPException(status_code=400, detail="Selecciona un dataset con imagenes validas")

        active_project_id = runtime_state.ingesta_info.get("project_id")
        active_job = job_store.active_job()
        if active_job and active_job.get("project_id") != active_project_id:
            raise HTTPException(status_code=409, detail="Primero termina el trabajo recibido que tiene el turno")
        existing_job = job_store.job_for_project(active_project_id) if active_project_id else None
        modelo_camara = (
            normalizar_modelo_camara(read_project_manifest(active_project_id).get("camera_model"))
            if existing_job else normalizar_modelo_camara(camera_model)
        )
        nombre_limpio = ensure_processing_project(nombre_proyecto, modelo_camara)
        job = job_store.job_for_project(nombre_limpio)

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
        env["METASHAPE_PROJECT_DIR"] = runtime_state.active_project_dir()
        env["METASHAPE_DATASET_DIR"] = runtime_state.active_dataset_dir()

        try:
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
        except OSError as exc:
            runtime_state.state.update(running=False, step="error", message=f"No se pudo iniciar Metashape: {exc}")
            raise HTTPException(status_code=500, detail=f"No se pudo iniciar Metashape: {exc}") from exc
        if job:
            try:
                job_store.set_state(job["organization_id"], job["batch_id"], "processing")
            except Exception as exc:
                runtime_state.push_log(f"No se pudo actualizar la cola local: {exc}")
        runtime_state.reader_thread = threading.Thread(target=reader_loop, args=(runtime_state.process, job), daemon=True)
        runtime_state.reader_thread.start()

    runtime_state.push_log("Solicitud de inicio recibida")
    return {
        "ok": True,
        "message": f"Proceso iniciado en segundo plano: {nombre_limpio}",
        "nombre_proyecto": nombre_limpio,
        "camera_model": modelo_camara,
    }


def detener_proceso():
    with runtime_state.lock:
        if not runtime_state.state["running"] or runtime_state.process is None:
            raise HTTPException(status_code=409, detail="No hay proceso en ejecucion")
        runtime_state.process.terminate()
        runtime_state.state["message"] = "Terminando proceso..."
    return {"ok": True, "message": "Se solicito detener el proceso"}
