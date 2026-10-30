import io
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

from backend.services.job_receiver import GeoFieldClient, JobReceiver, JobStore, download_zip


def zip_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("vuelo/foto.jpg", b"imagen de prueba")
    return output.getvalue()


class FakeResponse:
    def __init__(self, data, status=200, headers=None):
        self.data = data
        self.status_code = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_content(self, chunk_size):
        yield self.data


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.headers = None

    def get(self, _url, **kwargs):
        self.headers = kwargs["headers"]
        return self.response


class ReceiverTest(unittest.TestCase):
    def test_usa_autenticacion_y_rutas_existentes_de_geofield(self):
        class JsonResponse:
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class ApiSession:
            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append(("POST", url, kwargs))
                return JsonResponse({"access_token": "jwt-prueba", "refresh_token": "refresh",
                                     "expires_in": 3600})

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return JsonResponse([])

        session = ApiSession()
        client = GeoFieldClient(
            "https://geofield.example.com/api", supabase_url="https://auth.example.com",
            anon_key="anon", email="receptor@example.com", password="clave",
            session=session,
        )
        self.assertEqual(client.list_batches(), [])
        self.assertIn("grant_type=password", session.calls[0][1])
        self.assertEqual(session.calls[1][1],
                         "https://geofield.example.com/api/raw-uploads/manage/batches")
        self.assertEqual(session.calls[1][2]["headers"]["Authorization"], "Bearer jwt-prueba")

    def test_recibe_un_lote_una_vez_y_conserva_su_zip(self):
        org, batch, file_id = "org_prueba", str(uuid.uuid4()), str(uuid.uuid4())
        content = zip_bytes()
        summary = {
            "organizationId": org, "batchId": batch, "jobCode": "Vuelo 01",
            "organizationName": "Rancho", "createdByName": "Cliente",
            "completedAt": "2026-10-30T12:00:00Z", "fileCount": 1,
            "totalBytes": len(content), "status": "received",
        }
        file_info = {"fileId": file_id, "name": "fotos.zip",
                     "relativePath": "vuelo/fotos.zip", "size": len(content)}

        class FakeClient:
            def list_batches(self):
                return [summary, {**summary, "batchId": str(uuid.uuid4()),
                                  "status": "uploading", "completedAt": None}]

            def detail(self, *args):
                return {**summary, "files": [file_info], "uploadedFileIds": [file_id]}

            def download_url(self, *args):
                return "https://storage.example.com/fotos.zip"

        downloads = []

        def fake_download(url, destination, size):
            downloads.append((url, destination, size))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "incoming")
            receiver = JobReceiver(FakeClient(), store, downloader=fake_download)
            receiver.sync_once()
            receiver.sync_once()
            jobs = store.list_jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["state"], "ready")
            self.assertEqual(len(downloads), 1)
            files = store.list_files(org, batch)
            self.assertEqual(files[0]["name"], "fotos.zip")
            self.assertEqual(Path(files[0]["local_path"]).read_bytes(), content)
            self.assertEqual(JobStore(store.root).list_jobs()[0]["state"], "ready")

    def test_reanuda_descarga_parcial_y_valida_zip(self):
        content = zip_bytes()
        offset = len(content) // 2
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "fotos.zip"
            destination.with_name("fotos.zip.part").write_bytes(content[:offset])
            session = FakeSession(FakeResponse(
                content[offset:], 206,
                {"Content-Range": f"bytes {offset}-{len(content) - 1}/{len(content)}"},
            ))
            download_zip("https://storage.example.com/fotos.zip", destination,
                         len(content), session=session)
            self.assertEqual(session.headers, {"Range": f"bytes={offset}-"})
            self.assertEqual(destination.read_bytes(), content)
            self.assertFalse(destination.with_name("fotos.zip.part").exists())

    def test_no_acepta_zip_incompleto(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "fotos.zip"
            session = FakeSession(FakeResponse(b"faltan bytes"))
            with self.assertRaisesRegex(RuntimeError, "incompleto"):
                download_zip("https://storage.example.com/fotos.zip", destination,
                             100, session=session)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
