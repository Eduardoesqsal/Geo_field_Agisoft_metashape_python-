"""Registra ZIP locales en la cola del receptor para probar la interfaz."""

import argparse
import os
import shutil
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.job_receiver import JobStore


def registrar_trabajo(nombre, zip_paths, store=None):
    nombre = nombre.strip()
    if not nombre:
        raise ValueError("Escribe el nombre del proyecto")
    if not zip_paths:
        raise ValueError("Indica al menos un ZIP")

    archivos = []
    hay_imagenes = False
    for raw_path in zip_paths:
        path = Path(raw_path).expanduser().resolve()
        if path.suffix.lower() != ".zip" or not path.is_file():
            raise ValueError(f"No existe un ZIP válido: {path}")
        try:
            with zipfile.ZipFile(path) as archive:
                hay_imagenes |= any(
                    info.filename.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff"))
                    for info in archive.infolist() if not info.is_dir()
                )
        except zipfile.BadZipFile as exc:
            raise ValueError(f"El archivo no es un ZIP válido: {path}") from exc
        archivos.append((path, path.stat().st_size))
    if not hay_imagenes:
        raise ValueError("Los ZIP no contienen fotos JPG o TIFF")

    store = store or JobStore()
    org = "prueba_local"
    batch = str(uuid.uuid4())
    org_dir = store.root / org
    org_dir.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(org_dir).free < sum(size for _, size in archivos) + 128 * 1024 * 1024:
        raise ValueError("No hay espacio suficiente para copiar los ZIP")

    file_rows = []
    staging = org_dir / f".copiando_{batch}"
    staging.mkdir()
    try:
        for path, size in archivos:
            file_id = str(uuid.uuid4())
            destination = staging / f"{file_id}.zip"
            shutil.copyfile(path, destination)
            if destination.stat().st_size != size:
                raise OSError(f"La copia del ZIP quedó incompleta: {path}")
            file_rows.append(({
                "fileId": file_id,
                "name": path.name,
                "relativePath": path.name,
                "size": size,
            }, org_dir / batch / destination.name))
        os.replace(staging, org_dir / batch)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    store.add({
        "organizationId": org,
        "batchId": batch,
        "jobCode": nombre,
        "organizationName": "Prueba local",
        "createdByName": "Operador local",
        "completedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fileCount": len(file_rows),
        "totalBytes": sum(size for _, size in archivos),
    })
    for row, path in file_rows:
        store.add_file(org, batch, row, path)
    store.set_state(org, batch, "ready")
    return batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nombre", required=True, help="Nombre visible del proyecto")
    parser.add_argument("zip", nargs="+", help="Ruta de uno o más ZIP con fotos del vuelo")
    args = parser.parse_args()
    try:
        batch = registrar_trabajo(args.nombre, args.zip)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"Trabajo en espera: {args.nombre} (prueba_local/{batch})")
    print("Abre Cola de trabajos en http://127.0.0.1:8001")


if __name__ == "__main__":
    main()
