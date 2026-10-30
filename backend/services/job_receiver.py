"""Receives completed GeoField raw-upload batches on this computer.

This first stage only downloads ZIPs and records them locally. It does not
change remote batch status or start Metashape.
"""

import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from backend.core.paths import BASE_DIR


LOGGER = logging.getLogger(__name__)
INCOMING_DIR = Path(BASE_DIR) / "incoming"
ORG_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_url(url):
    parsed = urlparse(url)
    if not parsed.hostname or parsed.username or parsed.password:
        return False
    return parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    )


def _job_key(summary):
    org = str(summary["organizationId"])
    batch = str(uuid.UUID(str(summary["batchId"])))
    if not ORG_PATTERN.fullmatch(org):
        raise ValueError("Identificador de organización inválido")
    return org, batch


class JobStore:
    def __init__(self, root=INCOMING_DIR):
        self.root = Path(root)
        self.db_path = self.root / "jobs.sqlite3"

    @contextmanager
    def _connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=20)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
            organization_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            job_code TEXT NOT NULL,
            organization_name TEXT,
            created_by_name TEXT,
            completed_at TEXT,
            file_count INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            state TEXT NOT NULL,
            error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (organization_id, batch_id)
        )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS job_files (
            organization_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            name TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            size INTEGER NOT NULL,
            local_path TEXT NOT NULL,
            PRIMARY KEY (organization_id, batch_id, file_id)
        )""")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def add(self, summary):
        org, batch = _job_key(summary)
        with self._connect() as db:
            db.execute("""INSERT OR IGNORE INTO jobs
                (organization_id, batch_id, job_code, organization_name,
                 created_by_name, completed_at, file_count, total_bytes,
                 state, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?)""",
                (org, batch, str(summary.get("jobCode") or batch),
                 summary.get("organizationName"), summary.get("createdByName"),
                 summary.get("completedAt"), int(summary["fileCount"]),
                 int(summary["totalBytes"]), _now()))
        return org, batch

    def state(self, org, batch):
        with self._connect() as db:
            row = db.execute("SELECT state FROM jobs WHERE organization_id=? AND batch_id=?",
                             (org, batch)).fetchone()
        return row["state"] if row else None

    def set_state(self, org, batch, state, error=None):
        with self._connect() as db:
            db.execute("""UPDATE jobs SET state=?, error=?, updated_at=?
                          WHERE organization_id=? AND batch_id=?""",
                       (state, error, _now(), org, batch))

    def add_file(self, org, batch, file_info, path):
        with self._connect() as db:
            db.execute("""INSERT OR REPLACE INTO job_files
                (organization_id, batch_id, file_id, name, relative_path, size, local_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (org, batch, file_info["fileId"], file_info["name"],
                 file_info["relativePath"], int(file_info["size"]), str(path)))

    def list_jobs(self):
        with self._connect() as db:
            rows = db.execute("""SELECT * FROM jobs
                ORDER BY COALESCE(completed_at, updated_at), batch_id""").fetchall()
        return [dict(row) for row in rows]

    def list_files(self, org, batch):
        with self._connect() as db:
            rows = db.execute("""SELECT file_id, name, relative_path, size, local_path
                FROM job_files WHERE organization_id=? AND batch_id=? ORDER BY name""",
                (org, batch)).fetchall()
        return [dict(row) for row in rows]


class GeoFieldClient:
    def __init__(self, api_base, *, token=None, supabase_url=None, anon_key=None,
                 email=None, password=None, session=None):
        self.api_base = api_base.rstrip("/")
        if not _safe_url(self.api_base):
            raise ValueError("GEOFIELD_API_BASE_URL debe usar HTTPS")
        self.token = token
        self.supabase_url = (supabase_url or "").rstrip("/")
        self.anon_key = anon_key
        self.email = email
        self.password = password
        self.refresh_token = None
        self.expires_at = 0
        self.session = session or requests.Session()

    def _access_token(self):
        if self.token:
            return self.token
        if self.expires_at > time.time() + 30:
            return self._cached_token
        if not self.supabase_url or not self.anon_key or not self.email or not self.password:
            raise RuntimeError("Falta configurar la cuenta autorizada del receptor")
        if not _safe_url(self.supabase_url):
            raise ValueError("GEOFIELD_SUPABASE_URL debe usar HTTPS")
        if self.refresh_token:
            grant, body = "refresh_token", {"refresh_token": self.refresh_token}
        else:
            grant, body = "password", {"email": self.email, "password": self.password}
        response = self.session.post(
            f"{self.supabase_url}/auth/v1/token?grant_type={grant}",
            headers={"apikey": self.anon_key}, json=body, timeout=(10, 30),
        )
        if response.status_code >= 400:
            if grant == "refresh_token":
                self.refresh_token = None
                return self._access_token()
            raise RuntimeError(f"No se pudo autenticar el receptor: HTTP {response.status_code}")
        payload = response.json()
        self._cached_token = payload["access_token"]
        self.refresh_token = payload.get("refresh_token")
        self.expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._cached_token

    def _json(self, method, path, body=None):
        for attempt in range(2):
            response = self.session.request(
                method, f"{self.api_base}{path}",
                headers={"Authorization": f"Bearer {self._access_token()}"},
                json=body, timeout=(10, 60),
            )
            if response.status_code == 401 and not self.token and attempt == 0:
                self.expires_at = 0
                self.refresh_token = None
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"GeoField respondió HTTP {response.status_code} en {path}")
            return response.json()

    def list_batches(self):
        return self._json("GET", "/raw-uploads/manage/batches")

    def detail(self, org, batch):
        return self._json("GET", f"/raw-uploads/manage/batches/{org}/{batch}")

    def download_url(self, org, batch, file_id):
        result = self._json("POST", f"/raw-uploads/manage/batches/{org}/{batch}/download-urls",
                            {"fileIds": [file_id]})
        url = result["urls"][file_id]
        if not _safe_url(url):
            raise RuntimeError("GeoField entregó un enlace de descarga inseguro")
        return url


def download_zip(url, destination, expected_size, session=None):
    """Stream a ZIP to disk, resuming a complete interrupted HTTP range."""
    if not _safe_url(url):
        raise ValueError("Enlace de descarga inseguro")
    if expected_size <= 0:
        raise ValueError("El ZIP no tiene un tamaño válido")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    if destination.is_file() and destination.stat().st_size == expected_size:
        try:
            with zipfile.ZipFile(destination) as archive:
                archive.infolist()
            return
        except zipfile.BadZipFile:
            destination.unlink()
    offset = partial.stat().st_size if partial.exists() else 0
    if offset == expected_size:
        try:
            with zipfile.ZipFile(partial) as archive:
                archive.infolist()
            os.replace(partial, destination)
            return
        except zipfile.BadZipFile:
            partial.unlink()
            offset = 0
    if offset > expected_size:
        partial.unlink()
        offset = 0
    if shutil.disk_usage(destination.parent).free < expected_size - offset:
        raise RuntimeError("No hay espacio suficiente para descargar el ZIP")
    own_session = session is None
    session = session or requests.Session()
    try:
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with session.get(url, headers=headers, stream=True, timeout=(10, 120),
                             allow_redirects=False) as response:
                if response.status_code not in (200, 206):
                    raise RuntimeError(f"Descarga ZIP falló: HTTP {response.status_code}")
                if offset and response.status_code == 206:
                    expected_range = f"bytes {offset}-"
                    if not response.headers.get("Content-Range", "").startswith(expected_range):
                        raise RuntimeError("El servidor devolvió un rango incorrecto")
                else:
                    offset = 0
                with partial.open("ab" if offset else "wb") as output:
                    count = offset
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            count += len(chunk)
                            if count > expected_size:
                                raise RuntimeError("El ZIP recibido excede el tamaño declarado")
                            output.write(chunk)
        except requests.RequestException:
            raise RuntimeError("Error de red al descargar el ZIP") from None
        if partial.stat().st_size != expected_size:
            raise RuntimeError("El ZIP recibido está incompleto")
        with zipfile.ZipFile(partial) as archive:
            archive.infolist()
        os.replace(partial, destination)
    finally:
        if own_session:
            session.close()


class JobReceiver:
    def __init__(self, client, store, poll_seconds=30, downloader=download_zip):
        self.client = client
        self.store = store
        self.poll_seconds = max(5, int(poll_seconds))
        self.downloader = downloader
        self.stop_event = threading.Event()
        self.thread = None
        self.last_check = None
        self.error = None

    def sync_once(self):
        summaries = self.client.list_batches()
        if not isinstance(summaries, list):
            raise RuntimeError("GeoField no devolvió una lista de lotes")
        for summary in summaries:
            if summary.get("status") != "received" or not summary.get("completedAt"):
                continue
            try:
                org, batch = self.store.add(summary)
            except (KeyError, TypeError, ValueError) as exc:
                LOGGER.warning("GeoField devolvió un lote inválido: %s", exc)
                continue
            if self.store.state(org, batch) == "ready":
                continue
            try:
                detail = self.client.detail(org, batch)
                if detail.get("status") != "received" or not detail.get("completedAt"):
                    continue
                files = detail.get("files") or []
                if not files or len(files) != int(detail["fileCount"]):
                    raise RuntimeError("El manifiesto no coincide con la cantidad de archivos")
                if set(detail.get("uploadedFileIds") or []) != {f["fileId"] for f in files}:
                    raise RuntimeError("El lote aún no tiene todos sus archivos disponibles")
                self.store.set_state(org, batch, "downloading")
                for file_info in files:
                    file_id = str(uuid.UUID(str(file_info["fileId"])))
                    if not file_info["name"].lower().endswith(".zip"):
                        raise RuntimeError("El lote contiene un archivo que no es ZIP")
                    target = self.store.root / org / batch / f"{file_id}.zip"
                    self.downloader(self.client.download_url(org, batch, file_id),
                                    target, int(file_info["size"]))
                    self.store.add_file(org, batch, file_info, target)
                self.store.set_state(org, batch, "ready")
                LOGGER.info("Lote recibido: %s/%s (%d ZIP)", org, batch, len(files))
            except Exception as exc:
                self.store.set_state(org, batch, "error", str(exc))
                LOGGER.exception("No se pudo recibir el lote %s/%s", org, batch)
        self.last_check = _now()
        self.error = None

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.sync_once()
            except Exception as exc:
                self.error = str(exc)
                LOGGER.exception("Error consultando GeoField")
            self.stop_event.wait(self.poll_seconds)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="geofield-job-receiver", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)


_store = JobStore()
_receiver = None
_configuration_error = None


def start_receiver():
    global _receiver, _configuration_error
    base = os.environ.get("GEOFIELD_API_BASE_URL", "").strip()
    if not base:
        return
    token = os.environ.get("GEOFIELD_ACCESS_TOKEN")
    auth_values = {key: os.environ.get(key) for key in (
        "GEOFIELD_SUPABASE_URL", "GEOFIELD_SUPABASE_ANON_KEY",
        "GEOFIELD_WORKER_EMAIL", "GEOFIELD_WORKER_PASSWORD",
    )}
    if not token and not all(auth_values.values()):
        _configuration_error = "Falta configurar la cuenta autorizada de GeoField"
        return
    try:
        client = GeoFieldClient(
            base, token=token,
            supabase_url=auth_values["GEOFIELD_SUPABASE_URL"],
            anon_key=auth_values["GEOFIELD_SUPABASE_ANON_KEY"],
            email=auth_values["GEOFIELD_WORKER_EMAIL"],
            password=auth_values["GEOFIELD_WORKER_PASSWORD"],
        )
        _receiver = JobReceiver(client, _store, os.environ.get("GEOFIELD_POLL_SECONDS", "30"))
        _receiver.start()
        _configuration_error = None
    except (ValueError, TypeError) as exc:
        _configuration_error = str(exc)


def stop_receiver():
    if _receiver:
        _receiver.stop()


def queue_status():
    return {
        "enabled": bool(os.environ.get("GEOFIELD_API_BASE_URL")),
        "running": bool(_receiver and _receiver.thread and _receiver.thread.is_alive()),
        "last_check": _receiver.last_check if _receiver else None,
        "error": _receiver.error if _receiver else _configuration_error,
        "jobs": _store.list_jobs(),
    }


def queue_files(org, batch):
    org, batch = _job_key({"organizationId": org, "batchId": batch})
    return _store.list_files(org, batch)
