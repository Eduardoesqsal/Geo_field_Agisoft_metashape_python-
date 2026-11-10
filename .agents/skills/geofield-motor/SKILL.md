---
name: geofield-motor
description: Trabajar en el motor local GeoField de Agisoft Metashape, su UI React, cola de ZIP y entrega futura de ortomosaicos; usar al modificar o investigar este repositorio.
---

# GeoField motor local

Lee [README.md](../../../README.md) para arquitectura y uso. Consulta
[MEMORY.md](../../../MEMORY.md) para el estado y pendientes actuales. Los
contratos de recepción y entrega están en `docs/RECEPTOR_TRABAJOS.md` y
`docs/ENTREGA_RESULTADOS.md`; el README de producción recibido describe otro
repositorio y no equivale a una API de publicación de resultados.

## Rutas de trabajo

- Interfaz: `frontend/src/App.jsx`, `styles.css` y `utils/`. FastAPI sirve
  `frontend/dist/` desde `app.py`; construir el frontend tras cambios visibles.
- API/estado: `backend/routes.py`, `backend/core/state.py` y `catalog.py`.
- ZIP, cola y proyectos: `backend/services/job_receiver.py`, `job_handoff.py`,
  `projects.py` e `ingestion.py`. La cola SQLite está en `incoming/`.
- Metashape: `backend/services/process.py` lanza `backend/main.py`; la lógica
  de sensores y exportación vive en `backend/workflow.py`.
- Mapa/ROI: `backend/services/overlay.py`, `vector_overlay.py` y `clip.py`.

## Invariantes del proyecto

- Procesar un trabajo a la vez. Preparar un ZIP recibido crea proyecto y
  dataset, pero solo el botón existente inicia Metashape.
- Conservar asociación `organization_id + batch_id -> project_id + dataset_id`
  y nombre visible del trabajo. Los proyectos recibidos no deben cambiar de
  dataset durante su procesamiento.
- Respetar el orden de bandas Mavic 3 MS y MicaSense, la resolución MS actual
  de 0.20 m y el comportamiento RGB al cambiar integraciones.
- Conservar `dataset/`, `proyecto/`, `incoming/` y `logs/`; son datos reales
  ignorados por Git. No introducir secretos en archivos versionados.
- Distinguir cola local, receptor remoto y publicación: las credenciales del
  receptor habilitan descarga, no la subida del ortomosaico a producción.

Para verificar cambios de backend, ejecuta las pruebas pertinentes de
`tests/`; para frontend, `npm run build` en `frontend/`. Si el cambio depende de
Metashape real, valida además con un vuelo de prueba sin alterar otros sensores.
