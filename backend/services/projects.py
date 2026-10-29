"""Seleccion y gestion de proyectos y datasets locales."""

import os
import shutil

from fastapi import HTTPException

import backend.core.state as state
from backend.core.catalog import (
    dataset_path, image_count, list_datasets, list_projects,
    project_path, read_project_manifest, save_project_manifest, unique_id,
)
from backend.core.naming import normalizar_modelo_camara, sanitizar_nombre_proyecto
from backend.core.paths import DATASET_DIR, PROYECTO_DIR
from backend.services.ingestion import _actualizar_puntos_gps_desde_dataset


def _assert_idle():
    with state.lock:
        if state.state["running"]:
            raise HTTPException(status_code=409, detail="Espera a que termine el procesamiento")


def catalog_status():
    info = state.snapshot_ingesta_info()
    return {
        "proyectos": list_projects(),
        "datasets": list_datasets(),
        "project_id": info.get("project_id"),
        "dataset_id": info.get("dataset_id"),
    }


def create_project(nombre=None, camera_model=None):
    _assert_idle()
    info = state.snapshot_ingesta_info()
    dataset_id = info.get("dataset_id") or "legacy"
    name = sanitizar_nombre_proyecto(nombre or "proyecto")
    if os.path.exists(os.path.join(PROYECTO_DIR, name)) or any(
        item["id"] == f"legacy:{name}" for item in list_projects()
    ):
        raise HTTPException(status_code=409, detail="Ya existe un proyecto con ese nombre")
    model = normalizar_modelo_camara(camera_model or info.get("camera_model"))
    state.reset_runtime_state()
    state.update_ingesta_info(
        project_id=None, dataset_id=dataset_id, nombre_proyecto=name,
        camera_model=model, origen="dataset existente" if image_count(dataset_path(dataset_id), recursive=dataset_id != "legacy") else None,
    )
    _actualizar_puntos_gps_desde_dataset()
    state.update_state(message=f"Proyecto {name} listo para procesar")
    return {"ok": True, "mensaje": f"Proyecto {name} listo para procesar", "project_id": None,
            "nombre_proyecto": name, "camera_model": model}


def select_project(project_id):
    _assert_idle()
    item = next((item for item in list_projects() if item["id"] == project_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    manifest = read_project_manifest(project_id)
    name = item["nombre"]
    dataset_id = manifest.get("dataset_id") or "legacy"
    state.reset_runtime_state()
    state.update_ingesta_info(
        project_id=project_id, dataset_id=dataset_id, nombre_proyecto=name,
        camera_model=manifest.get("camera_model") or "mavic_3_rgb", origen="dataset existente",
        imagenes_validas=image_count(dataset_path(dataset_id), recursive=dataset_id != "legacy"),
    )
    _actualizar_puntos_gps_desde_dataset()
    if item["procesado"]:
        state.update_state(step="finalizado", message=f"Proyecto {name} cargado", returncode=0)
    else:
        state.update_state(message=f"Proyecto {name} listo")
    return {"ok": True, "mensaje": f"Proyecto {name} seleccionado", "project_id": project_id,
            "nombre_proyecto": name, "camera_model": state.camera_model_actual()}


def select_dataset(dataset_id):
    _assert_idle()
    item = next((item for item in list_datasets() if item["id"] == dataset_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Dataset no encontrado")
    state.update_ingesta_info(
        dataset_id=dataset_id, origen="dataset existente", imagenes_validas=item["imagenes_validas"],
        total_archivos=item["imagenes_validas"],
    )
    state.update_state(step="ingestado", message=f"Dataset {dataset_id} listo")
    _actualizar_puntos_gps_desde_dataset()
    info = state.snapshot_ingesta_info()
    project_id = info.get("project_id")
    if (project_id and not project_id.startswith("legacy:")
            and not os.path.isfile(os.path.join(project_path(project_id), f"{project_id}.psx"))):
        save_project_manifest(project_id, dataset_id, info.get("camera_model"))
    return {"ok": True, "mensaje": f"Dataset {item['nombre']} seleccionado", "dataset_id": dataset_id,
            "imagenes_validas": item["imagenes_validas"]}


def prepare_dataset_upload(nombre):
    _assert_idle()
    dataset_id = unique_id(DATASET_DIR, os.path.splitext(os.path.basename(nombre))[0] or "dataset")
    os.makedirs(dataset_path(dataset_id), exist_ok=False)
    state.update_ingesta_info(dataset_id=dataset_id)
    return dataset_id


def ensure_processing_project(nombre, camera_model):
    """Crea la carpeta al procesar y reutiliza la del proyecto seleccionado."""
    info = state.snapshot_ingesta_info()
    requested = sanitizar_nombre_proyecto(nombre or info.get("nombre_proyecto"))
    project_id = info.get("project_id")
    if project_id != requested or (project_id or "").startswith("legacy:"):
        if os.path.exists(os.path.join(PROYECTO_DIR, requested)) or any(
            item["id"] == f"legacy:{requested}" for item in list_projects()
        ):
            raise HTTPException(status_code=409, detail="Ya existe un proyecto con ese nombre; seleccionalo o usa otro nombre")
        previous_clip = state.active_clip_path()
        project_id = requested
        save_project_manifest(project_id, info.get("dataset_id") or "legacy", camera_model)
        if os.path.isfile(previous_clip):
            shutil.copy2(previous_clip, os.path.join(project_path(project_id), "recorte_ortomosaico.geojson"))
    else:
        save_project_manifest(project_id, info.get("dataset_id") or "legacy", camera_model)
    state.update_ingesta_info(project_id=project_id, nombre_proyecto=project_id, camera_model=camera_model)
    return project_id


def delete_project(project_id):
    _assert_idle()
    item = next((item for item in list_projects() if item["id"] == project_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    if project_id.startswith("legacy:"):
        raise HTTPException(status_code=400, detail="Los proyectos antiguos de la raiz no tienen una carpeta independiente")

    root = os.path.realpath(PROYECTO_DIR)
    candidate = os.path.join(PROYECTO_DIR, project_id)
    target = os.path.realpath(candidate)
    if (os.path.normcase(os.path.dirname(target)) != os.path.normcase(root)
            or os.path.islink(candidate)
            or (hasattr(os.path, "isjunction") and os.path.isjunction(candidate))
            or not os.path.isfile(os.path.join(candidate, "proyecto.json"))):
        raise HTTPException(status_code=400, detail="Ruta de proyecto no valida")

    shutil.rmtree(candidate)
    if state.snapshot_ingesta_info().get("project_id") == project_id:
        info = state.snapshot_ingesta_info()
        dataset_id = info.get("dataset_id") or "legacy"
        camera_model = info.get("camera_model")
        state.reset_runtime_state()
        state.update_ingesta_info(dataset_id=dataset_id, camera_model=camera_model)
        _actualizar_puntos_gps_desde_dataset()
        state.update_state(message=f"Proyecto {project_id} borrado")
    return {"ok": True, "mensaje": f"Proyecto {project_id} borrado"}
