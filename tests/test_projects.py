import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import backend.core.catalog as catalog
import backend.core.state as state
import backend.services.projects as projects
import backend.services.process as processing
import backend.services.overlay as overlay
from backend.services.ingestion import _extraer_zip_a_dataset
from backend.services.ingestion import _contar_imagenes_en_directorio
from backend.workflow import ProcesamientoMetashape


class MultiplesProyectosTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(state.reset_runtime_state)
        dataset_root = os.path.join(self.temporary.name, "dataset")
        project_root = os.path.join(self.temporary.name, "proyecto")
        os.makedirs(dataset_root)
        os.makedirs(project_root)
        self.project_root = project_root
        self.dataset_root = dataset_root
        patches = [
            patch.object(catalog, "DATASET_DIR", dataset_root),
            patch.object(catalog, "PROYECTO_DIR", project_root),
            patch.object(projects, "DATASET_DIR", dataset_root),
            patch.object(projects, "PROYECTO_DIR", project_root),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        state.reset_runtime_state()

    @staticmethod
    def _zip_fotos(nombre):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(nombre, b"imagen de prueba")
        buffer.seek(0)
        return buffer

    def test_conserva_datasets_y_permite_elegir_anteriores(self):
        first = projects.prepare_dataset_upload("vuelo.zip")
        _extraer_zip_a_dataset(self._zip_fotos("foto_1.jpg"))
        second = projects.prepare_dataset_upload("vuelo.zip")
        _extraer_zip_a_dataset(self._zip_fotos("foto_2.jpg"))

        self.assertNotEqual(first, second)
        self.assertTrue(os.path.isfile(os.path.join(catalog.dataset_path(first), "foto_1.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(catalog.dataset_path(second), "foto_2.jpg")))
        projects.select_dataset(first)
        self.assertEqual(state.active_dataset_dir(), catalog.dataset_path(first))
        self.assertEqual(len(catalog.list_datasets()), 2)

    def test_dataset_original_no_mezcla_fotos_de_los_nuevos(self):
        with open(os.path.join(self.dataset_root, "original.jpg"), "wb") as file:
            file.write(b"original")
        new_dataset = projects.prepare_dataset_upload("otro.zip")
        _extraer_zip_a_dataset(self._zip_fotos("nueva.jpg"))
        projects.select_dataset("legacy")
        with patch("backend.services.ingestion.DATASET_DIR", self.dataset_root), \
             patch("backend.workflow.DATASET_DIR", self.dataset_root), \
             patch("backend.workflow.RUTA_IMAGENES", self.dataset_root):
            _, validas = _contar_imagenes_en_directorio()
            rgb, _ = ProcesamientoMetashape()._clasificar_dataset()
        self.assertEqual([os.path.basename(path) for path in validas], ["original.jpg"])
        self.assertEqual([os.path.basename(path) for path in rgb], ["original.jpg"])
        self.assertTrue(os.path.isfile(os.path.join(catalog.dataset_path(new_dataset), "nueva.jpg")))

    def test_nueva_ejecucion_reutiliza_carpeta_y_conserva_recorte(self):
        dataset_id = projects.prepare_dataset_upload("fotos.zip")
        _extraer_zip_a_dataset(self._zip_fotos("foto.jpg"))
        projects.select_dataset(dataset_id)
        original = projects.create_project("Vuelo", "mavic_3_rgb")
        self.assertIsNone(original["project_id"])
        self.assertFalse(os.path.exists(catalog.project_path("Vuelo")))
        first_id = projects.ensure_processing_project("Vuelo", "mavic_3_rgb")
        original_dir = catalog.project_path(first_id)
        with open(os.path.join(original_dir, "Vuelo.psx"), "w", encoding="utf-8") as file:
            file.write("proyecto anterior")
        with open(os.path.join(original_dir, "recorte_ortomosaico.geojson"), "w", encoding="utf-8") as file:
            json.dump({"features": []}, file)

        next_id = projects.ensure_processing_project("Vuelo", "mavic_3_rgb")
        self.assertEqual(next_id, "Vuelo")
        self.assertTrue(os.path.isfile(os.path.join(original_dir, "Vuelo.psx")))
        self.assertTrue(os.path.isfile(os.path.join(original_dir, "recorte_ortomosaico.geojson")))
        self.assertEqual(catalog.read_project_manifest(next_id)["dataset_id"], dataset_id)
        self.assertEqual([item["id"] for item in catalog.list_projects()], ["Vuelo"])

        projects.select_project("Vuelo")
        self.assertEqual(state.snapshot_ingesta_info()["project_id"], "Vuelo")
        self.assertEqual(state.snapshot_ingesta_info()["dataset_id"], dataset_id)
        self.assertEqual(state.snapshot_state()["step"], "finalizado")
        self.assertEqual(overlay._ruta_ortomosaico_rgb(), os.path.join(original_dir, "Vuelo_rgb.tif"))
        self.assertEqual(state.active_clip_path(), os.path.join(original_dir, "recorte_ortomosaico.geojson"))

    def test_metashape_recibe_carpetas_del_proyecto_y_dataset(self):
        project_dir = os.path.join(self.temporary.name, "salida")
        dataset_dir = os.path.join(self.temporary.name, "fotos")
        env = os.environ.copy()
        env.update(METASHAPE_PROJECT_DIR=project_dir, METASHAPE_DATASET_DIR=dataset_dir,
                   METASHAPE_PROJECT_NAME="Vuelo")
        result = subprocess.run(
            [sys.executable, "-c", "import backend.config as c; print(c.RUTA_PROYECTO); print(c.RUTA_IMAGENES)"],
            cwd=os.path.dirname(os.path.dirname(__file__)), env=env,
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip().splitlines(), [os.path.join(project_dir, "Vuelo.psx"), dataset_dir])

    def test_procesamientos_sucesivos_usan_la_misma_carpeta(self):
        dataset_id = projects.prepare_dataset_upload("fotos.zip")
        _extraer_zip_a_dataset(self._zip_fotos("foto.jpg"))
        projects.select_dataset(dataset_id)
        projects.create_project("Vuelo", "mavic_3_rgb")
        executable = os.path.join(self.temporary.name, "metashape.exe")
        with open(executable, "wb"):
            pass
        environments = []

        class FakeProcess:
            stdout = []

            def poll(self):
                return 0

        def fake_popen(*args, **kwargs):
            environments.append(kwargs["env"])
            return FakeProcess()

        with patch.object(processing, "METASHAPE_EXE", executable), \
             patch.object(processing.subprocess, "Popen", side_effect=fake_popen), \
             patch.object(processing, "_sincronizar_overlay_proyecto"):
            processing.iniciar_proceso("Vuelo", "mavic_3_rgb")
            state.reader_thread.join(timeout=5)
            first_id = state.snapshot_ingesta_info()["project_id"]
            with open(os.path.join(catalog.project_path(first_id), f"{first_id}.psx"), "w"):
                pass
            processing.iniciar_proceso("Vuelo", "mavic_3_rgb")
            state.reader_thread.join(timeout=5)

        self.assertEqual(environments[0]["METASHAPE_PROJECT_DIR"], environments[1]["METASHAPE_PROJECT_DIR"])
        self.assertEqual(environments[0]["METASHAPE_DATASET_DIR"], catalog.dataset_path(dataset_id))
        self.assertEqual(environments[1]["METASHAPE_DATASET_DIR"], catalog.dataset_path(dataset_id))
        self.assertEqual([item["id"] for item in catalog.list_projects()], ["Vuelo"])

    def test_borrar_proyecto_conserva_dataset_y_limpia_seleccion(self):
        dataset_id = projects.prepare_dataset_upload("vuelo.zip")
        _extraer_zip_a_dataset(self._zip_fotos("foto.jpg"))
        projects.select_dataset(dataset_id)
        projects.create_project("Vuelo", "mavic_3_rgb")
        projects.ensure_processing_project("Vuelo", "mavic_3_rgb")
        project_dir = catalog.project_path("Vuelo")
        with open(os.path.join(project_dir, "Vuelo.psx"), "w"):
            pass

        projects.delete_project("Vuelo")

        self.assertFalse(os.path.exists(project_dir))
        self.assertTrue(os.path.exists(catalog.dataset_path(dataset_id)))
        self.assertIsNone(state.snapshot_ingesta_info()["project_id"])
        self.assertEqual(state.snapshot_ingesta_info()["dataset_id"], dataset_id)
        self.assertEqual(catalog.list_projects(), [])


if __name__ == "__main__":
    unittest.main()
