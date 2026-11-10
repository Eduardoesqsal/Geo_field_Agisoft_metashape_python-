"""Prepara en la interfaz local un lote de ZIP ya recibido.

La preparacion crea un proyecto y su dataset, pero no inicia Metashape.
"""

import os
import shutil
import stat
import threading
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

import backend.core.state as state
from backend.core.catalog import image_count, save_project_manifest, unique_id
from backend.core.naming import normalizar_modelo_camara
from backend.core.paths import DATASET_DIR, PROYECTO_DIR
from backend.services.ingestion import _actualizar_puntos_gps_desde_dataset
from backend.services.job_receiver import _job_key, _store


def _zip_paths(store, org, batch, expected_count):
    files = store.list_files(org, batch)
    if not files or len(files) != expected_count:
        raise RuntimeError("El lote no tiene todos sus ZIP descargados")
    base = (store.root / org / batch).resolve()
    paths = []
    for item in files:
        file_path = Path(item["local_path"])
        if (file_path.is_symlink() or file_path.resolve().parent != base
                or file_path.name != f"{item['file_id']}.zip"
                or not file_path.is_file() or file_path.stat().st_size != item["size"]):
            raise RuntimeError("Un ZIP recibido no coincide con el manifiesto local")
        paths.append(file_path)
    return paths


def _extract_images(paths, target):
    members = []
    members_by_zip = {}
    total_bytes = 0
    for index, zip_path in enumerate(paths, 1):
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                raw = info.filename.replace("\\", "/")
                parts = PurePosixPath(raw).parts
                if (not parts or raw.startswith("/") or any(part in {"", ".", ".."} for part in parts)
                        or any(":" in part for part in parts)
                        or stat.S_ISLNK(info.external_attr >> 16)
                        or info.flag_bits & 1):
                    raise RuntimeError("El ZIP contiene una ruta o archivo no permitido")
                relative = Path(f"zip_{index:03d}", *parts)
                members.append((zip_path, info, relative))
                members_by_zip.setdefault(zip_path, []).append((info, relative))
                total_bytes += info.file_size
    if len(members) > 100_000:
        raise RuntimeError("El lote contiene demasiados archivos")
    if len({str(relative).lower() for _, _, relative in members}) != len(members):
        raise RuntimeError("El lote contiene rutas duplicadas")
    if shutil.disk_usage(target.parent).free < total_bytes + 128 * 1024 * 1024:
        raise RuntimeError("No hay espacio suficiente para extraer las fotos")

    for zip_path in paths:
        with zipfile.ZipFile(zip_path) as archive:
            for info, relative in members_by_zip.get(zip_path, []):
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(destination, "xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    if image_count(str(target), recursive=True) == 0:
        raise RuntimeError("El lote no contiene imagenes JPG o TIFF")


def _prepare_worker(store, org, batch, camera_model):
    stage = None
    dataset_dir = None
    project_dir = None
    linked = False
    try:
        job = store.get_job(org, batch)
        if job is None or job["state"] != "preparing":
            raise RuntimeError("El trabajo ya no esta en preparacion")
        paths = _zip_paths(store, org, batch, job["file_count"])
        Path(DATASET_DIR).mkdir(parents=True, exist_ok=True)
        project_id = unique_id(PROYECTO_DIR, job["job_code"])
        dataset_id = unique_id(DATASET_DIR, f"{project_id}_fotos")
        stage = Path(DATASET_DIR) / f".{dataset_id}_{batch}.preparando"
        dataset_dir = Path(DATASET_DIR) / dataset_id
        project_dir = Path(PROYECTO_DIR) / project_id
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        _extract_images(paths, stage)
        os.replace(stage, dataset_dir)
        stage = None
        save_project_manifest(project_id, dataset_id, camera_model)
        store.link_project(org, batch, project_id, dataset_id)
        linked = True

        state.update_ingesta_info(
            project_id=project_id, dataset_id=dataset_id,
            nombre_proyecto=project_id, camera_model=camera_model,
            origen="trabajo recibido", imagenes_validas=image_count(str(dataset_dir), recursive=True),
        )
        try:
            _actualizar_puntos_gps_desde_dataset()
        except Exception as exc:
            state.push_log(f"No se pudieron leer todos los GPS del trabajo: {exc}")
        state.update_state(running=False, step="ingestado",
                           message=f"Trabajo {job['job_code']} listo para configurar ROI y procesar")
        state.push_log(f"Trabajo recibido preparado: {job['job_code']} ({project_id})")
    except Exception as exc:
        if not linked:
            for path in (stage, dataset_dir, project_dir):
                if path is not None and path.exists():
                    try:
                        shutil.rmtree(path)
                    except OSError as cleanup_error:
                        state.push_log(f"No se pudo limpiar {path.name}: {cleanup_error}")
            try:
                store.set_state(org, batch, "failed", str(exc))
            except Exception as queue_error:
                state.push_log(f"No se pudo actualizar la cola local: {queue_error}")
        state.update_state(running=False, step="error", message=f"No se pudo preparar el trabajo: {exc}", error=str(exc))
        state.push_log(f"No se pudo preparar el trabajo recibido: {exc}")


def prepare_received_job(organization_id, batch_id, camera_model):
    try:
        org, batch = _job_key({"organizationId": organization_id, "batchId": batch_id})
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail="Identificador de trabajo invalido") from exc
    if camera_model not in {"mavic_3_rgb", "mavic_3m", "rededge_m"}:
        raise HTTPException(status_code=400, detail="Selecciona un sensor valido")
    camera_model = normalizar_modelo_camara(camera_model)

    with state.lock:
        if state.state["running"]:
            raise HTTPException(status_code=409, detail="Espera a que termine el trabajo activo")
        if state.ingesta_info.get("project_id"):
            raise HTTPException(status_code=409, detail="Guarda o cierra el proyecto actual antes de abrir otro trabajo")
        if not _store.claim_next(org, batch):
            raise HTTPException(status_code=409, detail="Este trabajo aun no tiene el turno disponible")
        state.state.update(running=True, step="preparando_dataset",
                           message="Extrayendo las fotos del trabajo recibido", error=None)

    thread = threading.Thread(target=_prepare_worker, args=(_store, org, batch, camera_model), daemon=True)
    thread.start()
    return {"ok": True, "mensaje": "Preparando proyecto y dataset", "organization_id": org,
            "batch_id": batch, "state": "preparing"}
