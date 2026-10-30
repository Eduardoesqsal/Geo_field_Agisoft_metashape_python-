"""Rutas y catalogo persistente de proyectos y datasets locales."""

import json
import os
from datetime import datetime

from backend.core.naming import sanitizar_nombre_proyecto
from backend.core.paths import DATASET_DIR, PROYECTO_DIR


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff"}
MANIFEST_NAME = "proyecto.json"


def unique_id(root, desired):
    base = sanitizar_nombre_proyecto(desired)
    candidate = base
    number = 2
    while os.path.exists(os.path.join(root, candidate)):
        candidate = f"{base}_{number}"
        number += 1
    return candidate


def project_path(project_id):
    if not project_id or project_id.startswith("legacy:"):
        return PROYECTO_DIR
    return os.path.join(PROYECTO_DIR, sanitizar_nombre_proyecto(project_id))


def project_name(project_id):
    return project_id.removeprefix("legacy:") if project_id else None


def dataset_path(dataset_id):
    if not dataset_id or dataset_id == "legacy":
        return DATASET_DIR
    return os.path.join(DATASET_DIR, sanitizar_nombre_proyecto(dataset_id))


def image_count(path, *, recursive=False):
    if not os.path.isdir(path):
        return 0
    if recursive:
        return sum(
            os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS
            for _, _, files in os.walk(path) for name in files
        )
    return sum(
        os.path.isfile(os.path.join(path, name)) and os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS
        for name in os.listdir(path)
    )


def list_datasets():
    datasets = []
    legacy_count = image_count(DATASET_DIR)
    if legacy_count:
        datasets.append({"id": "legacy", "nombre": "Dataset original", "imagenes_validas": legacy_count})
    if os.path.isdir(DATASET_DIR):
        for name in sorted(os.listdir(DATASET_DIR)):
            path = os.path.join(DATASET_DIR, name)
            if os.path.isdir(path):
                count = image_count(path, recursive=True)
                if count:
                    datasets.append({"id": name, "nombre": name, "imagenes_validas": count})
    return datasets


def save_project_manifest(project_id, dataset_id, camera_model):
    path = project_path(project_id)
    os.makedirs(path, exist_ok=True)
    previous = read_project_manifest(project_id)
    manifest = {
        "id": project_id,
        "dataset_id": dataset_id,
        "camera_model": camera_model,
        "created_at": previous.get("created_at") or datetime.now().isoformat(timespec="seconds"),
    }
    temporary = os.path.join(path, MANIFEST_NAME + ".tmp")
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(manifest, file)
    os.replace(temporary, os.path.join(path, MANIFEST_NAME))
    return manifest


def read_project_manifest(project_id):
    if project_id.startswith("legacy:"):
        return {"id": project_id, "dataset_id": "legacy", "camera_model": "mavic_3_rgb"}
    path = os.path.join(project_path(project_id), MANIFEST_NAME)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    return {"id": project_id, "dataset_id": "legacy", "camera_model": "mavic_3_rgb"}


def list_projects():
    projects = []
    if not os.path.isdir(PROYECTO_DIR):
        return projects
    for name in sorted(os.listdir(PROYECTO_DIR)):
        path = os.path.join(PROYECTO_DIR, name)
        if os.path.isdir(path):
            manifest_path = os.path.join(path, MANIFEST_NAME)
            if not os.path.isfile(manifest_path):
                continue
            manifest = read_project_manifest(name)
            ortomosaico_listo = any(
                os.path.isfile(os.path.join(path, f"{name}_{tipo}.tif"))
                for tipo in ("rgb", "ms")
            )
            projects.append({
                **manifest,
                "nombre": name,
                "procesado": os.path.isfile(os.path.join(path, f"{name}.psx")),
                "ortomosaico_listo": ortomosaico_listo,
                "ruta": path,
            })
        elif name.lower().endswith(".psx"):
            stem = os.path.splitext(name)[0]
            projects.append({
                "id": f"legacy:{stem}", "nombre": stem,
                "dataset_id": "legacy", "camera_model": "mavic_3_rgb",
                "procesado": True,
                "ortomosaico_listo": any(
                    os.path.isfile(os.path.join(PROYECTO_DIR, f"{stem}_{tipo}.tif"))
                    for tipo in ("rgb", "ms")
                ),
                "ruta": PROYECTO_DIR,
            })
    return projects
