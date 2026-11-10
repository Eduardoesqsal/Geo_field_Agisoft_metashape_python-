import io
import os
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import backend.core.catalog as catalog
import backend.core.state as state
from backend.services import job_handoff
import backend.services.process as processing
from backend.services.job_receiver import JobStore


def make_zip(name, content=b"foto de prueba"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, content)
    return output.getvalue()


class JobHandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(state.reset_runtime_state)
        self.root = Path(self.temp.name)
        self.dataset_root = self.root / "dataset"
        self.project_root = self.root / "proyecto"
        self.dataset_root.mkdir()
        self.project_root.mkdir()
        self.store = JobStore(self.root / "incoming")
        for target, name, value in (
            (catalog, "DATASET_DIR", str(self.dataset_root)),
            (catalog, "PROYECTO_DIR", str(self.project_root)),
            (job_handoff, "DATASET_DIR", str(self.dataset_root)),
            (job_handoff, "PROYECTO_DIR", str(self.project_root)),
            (job_handoff, "_store", self.store),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        state.reset_runtime_state()

    def add_job(self, job_code, files, completed_at):
        org, batch = "org_prueba", str(uuid.uuid4())
        self.store.add({
            "organizationId": org, "batchId": batch, "jobCode": job_code,
            "completedAt": completed_at, "fileCount": len(files),
            "totalBytes": sum(len(data) for _, data in files),
        })
        for name, data in files:
            file_id = str(uuid.uuid4())
            path = self.store.root / org / batch / f"{file_id}.zip"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.store.add_file(org, batch, {
                "fileId": file_id, "name": name, "relativePath": name, "size": len(data),
            }, path)
        self.store.set_state(org, batch, "ready")
        return org, batch

    def test_prepara_el_primer_trabajo_y_conserva_fotos_de_varios_zip(self):
        first = self.add_job("Cafe Veracruz", [
            ("parte1.zip", make_zip("vuelo/foto.jpg", b"primera")),
            ("parte2.zip", make_zip("vuelo/foto.jpg", b"segunda")),
        ], "2026-11-10T10:00:00Z")
        second = self.add_job("Cafe siguiente", [
            ("fotos.zip", make_zip("foto.jpg")),
        ], "2026-11-10T11:00:00Z")

        self.assertFalse(self.store.claim_next(*second))
        self.assertTrue(self.store.claim_next(*first))
        job_handoff._prepare_worker(self.store, *first, "mavic_3m")

        job = self.store.get_job(*first)
        self.assertEqual(job["state"], "prepared")
        self.assertEqual(job["project_id"], "Cafe_Veracruz")
        self.assertEqual(catalog.read_project_manifest(job["project_id"])["camera_model"], "mavic_3m")
        dataset = self.dataset_root / job["dataset_id"]
        self.assertEqual((dataset / "zip_001" / "vuelo" / "foto.jpg").read_bytes(), b"primera")
        self.assertEqual((dataset / "zip_002" / "vuelo" / "foto.jpg").read_bytes(), b"segunda")
        self.assertEqual(state.snapshot_ingesta_info()["project_id"], job["project_id"])
        self.assertEqual(state.snapshot_ingesta_info()["imagenes_validas"], 2)
        self.assertIsNone(self.store.next_ready())
        self.assertFalse(self.store.claim_next(*second))

        self.store.set_state(*first, "done")
        self.assertEqual(self.store.next_ready()["batch_id"], second[1])

    def test_zip_con_ruta_insegura_no_escribe_fuera_del_dataset(self):
        job_key = self.add_job("Trabajo", [("fotos.zip", make_zip("../fuera.jpg"))],
                               "2026-11-10T10:00:00Z")
        self.assertTrue(self.store.claim_next(*job_key))

        job_handoff._prepare_worker(self.store, *job_key, "mavic_3_rgb")

        self.assertEqual(self.store.get_job(*job_key)["state"], "failed")
        self.assertFalse((self.root / "fuera.jpg").exists())
        self.assertEqual(list(self.project_root.iterdir()), [])

    def test_boton_de_procesar_actualiza_el_turno_local(self):
        job_key = self.add_job("Vuelo recibido", [("fotos.zip", make_zip("foto.jpg"))],
                               "2026-11-10T10:00:00Z")
        self.assertTrue(self.store.claim_next(*job_key))
        job_handoff._prepare_worker(self.store, *job_key, "mavic_3_rgb")
        project_id = self.store.get_job(*job_key)["project_id"]

        executable = self.root / "metashape.exe"
        main_script = self.root / "main.py"
        executable.touch()
        main_script.touch()

        class FakeProcess:
            stdout = iter(["[1/5] Cargando fotos\n"])

            def poll(self):
                return 0

        with patch.object(processing, "METASHAPE_EXE", str(executable)), \
             patch.object(processing, "MAIN_SCRIPT", str(main_script)), \
             patch.object(processing, "job_store", self.store), \
             patch.object(processing.subprocess, "Popen", return_value=FakeProcess()), \
             patch.object(processing, "_sincronizar_overlay_proyecto"):
            processing.iniciar_proceso(project_id, "mavic_3_rgb")
            state.reader_thread.join(timeout=5)

        self.assertEqual(self.store.get_job(*job_key)["state"], "done")
        self.assertEqual(state.snapshot_state()["step"], "finalizado")


if __name__ == "__main__":
    unittest.main()
