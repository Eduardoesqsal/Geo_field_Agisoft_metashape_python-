# Project Notes for Agents

## What this repo is

This repository is a **FastAPI backend** plus a **React frontend** for an
Agisoft Metashape photogrammetry processing workflow.

The app:
- ingests imagery from `dataset/`, ZIP uploads, or Google Drive
- runs Agisoft Metashape in the background
- exports RGB and multispectral orthomosaics to `proyecto/`
- serves an interactive web UI from `frontend/`
- renders the final RGB ortho as a tiled overlay on a Leaflet map
- allows starting a fresh project after a finished run

## Arquitectura general

```
Cliente (navegador)             Render (Static Site)           PC local (Windows + Metashape)
       │                              │                                │
       ├── https://url.render.com ───►│                                │
       │                              │ Sirve frontend estático       │
       │                              │                                │
       │   ┌── API calls ─────────────┼──────────────────────────────►│
       │   │  (status, logs,          │     ngrok / IP local           │
       │   │   ingesta, procesar,     │                                │
       │   │   tiles)                 │                                │
       │   │                          │                                │
       │   ◄── resultados ────────────┼────────────────────────────────┤
       │                              │                                │
```

### Arquitectura híbrida

- **Render** hostea el frontend compilado (Static Site o Web Service Docker)
- **PC local (Windows)** corre el backend FastAPI + Metashape
- El frontend obtiene la URL del backend via `/config.js` → `API_BASE_URL` (env)
- La comunicación PC ↔ Render se hace por túnel (ngrok) o IP local (misma red)
- CORS está configurado en `backend/application.py` para permitir orígenes cruzados

## Estructura del proyecto

```
.
├── app.py                          # Entry point: uvicorn app:app --host 0.0.0.0 --port 8000
├── auto_url.py                     # Script que detecta URL de ngrok y actualiza Render API
├── iniciar_servidor.bat            # Windows batch: arranca backend + ngrok + auto_url
│
├── backend/
│   ├── __init__.py
│   ├── application.py              # FastAPI factory + CORS middleware
│   ├── config.py                   # Rutas fijas: dataset, proyecto, ortomosaicos
│   ├── main.py                     # Metashape entry script (ejecutado por metashape.exe -r)
│   ├── routes.py                   # Todos los endpoints HTTP + /config.js
│   ├── runtime.py                  # Estado compartido, logs, cache, helpers
│   ├── workflow.py                 # Pipeline Metashape: clase ProcesamientoMetashape
│   └── services/
│       ├── __init__.py
│       ├── ingestion.py            # ZIP / Google Drive ingestion
│       ├── overlay.py              # Tile generation, PNG, shapefile overlay
│       └── process.py              # Lanzar/detener proceso Metashape (subprocess)
│
├── frontend/
│   ├── index.html                  # HTML + <script src="/config.js"> para runtime config
│   ├── vite.config.js              # Vite config + proxy para dev
│   ├── package.json                # Dependencias React + pnpm.onlyBuiltDependencies
│   ├── pnpm-lock.yaml
│   └── src/
│       ├── main.jsx                # Entry point React
│       ├── App.jsx                 # UI completa: mapa Leaflet, controles, logs
│       └── styles.css              # Todos los estilos
│
├── dataset/                        # Imágenes de entrada (ignorado por git)
├── datasets_crudos/                # Datos crudos grandes (ignorado por git)
├── proyecto/                       # Salidas: .psx, *_rgb.tif, *_ms.tif (ignorado por git)
├── logs/                           # Logs del backend y ngrok (ignorado por git)
│
├── Dockerfile                      # Build multi-etapa: Node 24 → Python 3.11
├── .github/workflows/ci-cd.yml     # CI/CD: frontend build, backend syntax, docker, deploy
├── .dockerignore
├── .gitignore
├── requirements.txt                # fastapi, uvicorn, rasterio, pillow, geopandas, etc.
├── README.md                       # Documentación completa
└── AGENTS.md                       # Este archivo
```

## Frontend (`frontend/`)

### Tecnologías
- **React 18** + **Vite 5** + **pnpm** (via corepack)
- **Leaflet** para mapa interactivo con Bing Aerial base layer
- **CSS plano** sin frameworks

### Runtime config (clave para deploy híbrido)

`App.jsx` usa `window.API_BASE` como base URL para todas las llamadas API.
Este valor lo inyecta el backend en `/config.js` leyendo la env var `API_BASE_URL`.

```
Flujo:
  index.html → <script src="/config.js"> → window.API_BASE = "https://..."
  → todas las fetch() usan API_BASE + path
  → tiles de ortomosaico usan API_BASE + /tiles/rgb/{z}/{x}/{y}.png
```

### API calls desde frontend

Todas las llamadas pasan por `apiJson(path)` que construye `${API_BASE}${path}`.
Incluye:
- `GET /status`, `/logs`, `/ingesta/estado`, `/overlay/status`
- `POST /ingesta/zip` (via XMLHttpRequest con progreso)
- `POST /ingesta/drive`, `/proyecto/nombre`, `/proyecto/nuevo`
- `POST /procesar`, `/start`, `/stop`

### Zonas del mapa
- **pre-final**: muestra puntos GPS como círculos azules
- **final** (procesamiento terminado): muestra el ortomosaico como tiles overlay
- Scroll zoom nativo de Leaflet, sin listeners wheel personalizados

## Backend (`backend/`)

### Tecnologías
- **FastAPI** + **Uvicorn**
- **rasterio**, **PIL/Pillow**, **geopandas** para procesamiento geoespacial
- **subprocess** para lanzar Metashape.exe

### CORS
Configurado en `application.py` con `allow_origins=["*"]` para permitir
peticiones desde Render hacia el backend local.

### Endpoints (`routes.py`)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/config.js` | Retorna `window.API_BASE = "..."` desde `API_BASE_URL` env |
| `GET` | `/status` | Estado del proceso (running, step, etc.) |
| `GET` | `/logs` | Logs del proceso actual |
| `GET` | `/ingesta/estado` | Estado de la ingesta (archivos, GPS, proyecto) |
| `POST` | `/ingesta/zip` | Sube y extrae ZIP a `dataset/` |
| `POST` | `/ingesta/drive` | Descarga ZIP desde Google Drive |
| `POST` | `/proyecto/nombre` | Guarda nombre del proyecto |
| `POST` | `/proyecto/nuevo` | Resetea proyecto + runtime state |
| `POST` | `/procesar` | Inicia procesamiento Metashape |
| `POST` | `/start` | Inicia procesamiento (alias) |
| `POST` | `/stop` | Detiene proceso en ejecución |
| `GET` | `/overlay/status` | Estado del overlay (bounds, disponible) |
| `GET` | `/overlay/rgb.png` | PNG del ortomosaico RGB (stretched) |
| `GET` | `/tiles/rgb/{z}/{x}/{y}.png` | Tiles del ortomosaico |
| `GET` | `/{path}` | Sirve archivos estáticos del frontend |

### Process launch (`services/process.py`)

- Valida que `METASHAPE_EXE` exista y haya imágenes válidas
- Lanza `metashape.exe -r backend/main.py` como subprocess
- Lee stdout en tiempo real, parsea pasos `[1/6]` a `[6/6]`
- Al terminar: sincroniza overlay
- `detener_proceso()` envía `terminate()` al subprocess

### Metashape workflow (`workflow.py`)

Clase `ProcesamientoMetashape` con pipeline de 6 pasos:
1. `cargar_fotos()` — clasifica JPG (RGB) y TIFF (MS) en chunks separados
2. `alinear_camaras()` — matchPhotos + alignCameras
3. `construir_profundidad()` — buildDepthMaps
4. `construir_modelo()` — buildModel
5. `construir_ortomosaico()` — buildDem + buildOrthomosaic
6. `exportar_resultado()` — exporta a `*_rgb.tif` y `*_ms.tif`

Soporta dos modelos de cámara:
- `mavic_3m` — DJI Mavic 3 Multispectral
- `rededge_m` — MicaSense RedEdge-M

### State management (`runtime.py`)

Estado en memoria con locks (`threading.Lock`):
- `state`: running, step, message, timestamps, returncode
- `logs`: lista circular (máx 800 líneas)
- `ingesta_info`: origen, archivos, GPS, nombre proyecto, modelo cámara
- `overlay_cache`: PNG, bounds, cache_buster
- `shapefile_overlay_cache`: GeoJSON para shapefiles

Funciones: `snapshot_*()`, `update_*()`, `push_log()`, `reset_runtime_state()`

### Ingestion (`services/ingestion.py`)

- `_extraer_zip_a_dataset()`: extrae ZIP a `dataset/`, clasifica imágenes
- `_descargar_zip_drive()`: descarga desde Google Drive usando requests
- Escanea `dataset/` y cuenta archivos válidos (JPG, TIFF)
- Lee GPS de EXIF y calcula centro/ bounding box

### Overlay (`services/overlay.py`)

- Descubre `*_rgb.tif` y `*_ms.tif` en `proyecto/`
- Lee bounds del raster con rasterio
- Genera PNG (stretched 2-98%) para vista previa RGB
- Sirve tiles 256x256 del ortomosaico
- Soporta shapefiles como overlay vectorial

## CI/CD (`.github/workflows/ci-cd.yml`)

```yaml
jobs:
  frontend:     # Node 24 + pnpm install + build
  backend:      # Python 3.11 + compileall syntax check
  docker:       # Docker build (depende de frontend + backend)
  deploy:       # Render deploy hook (solo push a main)
```

### Frontend build en CI
- Se removió `cache: pnpm` de `setup-node` (causaba error)
- Se activa corepack y pnpm explícitamente
- Se usa `--ignore-scripts` en install (build scripts se corren en build)
- `onlyBuiltDependencies: ["esbuild"]` en package.json

### Deploy
- Dispara webhook de Render con `RENDER_DEPLOY_HOOK_URL` (secret)
- Solo en push a `main`

## Docker (`Dockerfile`)

Multi-etapa:
1. `node:24-bookworm-slim` → compila frontend (pnpm install --ignore-scripts + pnpm build)
2. `python:3.11-slim-bookworm` → instala GDAL, dependencias Python, copia backend + frontend/dist
- Puerto: `10000` (configurable via `$PORT`)
- Comando: `uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}`

## Variables de entorno

| Variable | Dónde se usa | Propósito |
|---|---|---|
| `API_BASE_URL` | `routes.py` → `/config.js` | URL del backend local (ngrok o IP) |
| `RENDER_API_KEY` | `auto_url.py` | API key de Render para actualizar env vars |
| `RENDER_SERVICE_ID` | `auto_url.py` | ID del servicio en Render |
| `METASHAPE_EXE` | `runtime.py` | Ruta al ejecutable de Metashape |
| `METASHAPE_PROJECT_NAME` | `config.py` | Nombre del proyecto Metashape |
| `PORT` | `Dockerfile` | Puerto del servidor (default 10000) |

## Security notes

- `allow_origins=["*"]` en CORS — deliberado para entorno híbrido
- Sin autenticación — asume red WiFi privada
- Sin rate limiting — no apto para internet público sin protección
- Sin HTTPS — el túnel (ngrok) provee HTTPS, pero sin túnel los datos van en texto plano

## Practical workflows

### Local (desarrollo)
```bash
python app.py                                    # backend en :8000
cd frontend && corepack pnpm dev                 # frontend en :5173 con proxy
```

### Producción (híbrido Render + PC local)
```bash
python app.py                                    # backend en :8000
ngrok http 8000                                  # túnel HTTPS público
# Render sirve frontend, API_BASE_URL = ngrok URL
```

### Auto-start (Windows)
- `iniciar_servidor.bat` arranca backend + ngrok + actualiza Render
- Ponlo en `shell:startup` para que arranque al encender el PC
- `auto_url.py` lee la URL de ngrok desde `localhost:4040/api/tunnels` y actualiza Render API

### Deploy / CI
```bash
git add . && git commit -m "mensaje" && git push
# GitHub Actions corre: frontend build → backend syntax → docker → deploy
```

### Clean slate (nuevo proyecto)
```bash
POST /proyecto/nuevo
# Resetea: estado, logs, ingesta_info, overlay_cache, elimina .psx .tif de proyecto/
```

## Frontend rules

- Use `pnpm` through `corepack` for frontend commands.
- Run installs with: `corepack pnpm install`
- Build with: `corepack pnpm build`
- Dev server with: `corepack pnpm dev`
- Keep visual changes in `frontend/src/App.jsx` and `frontend/src/styles.css`.
- Do not duplicate backend logic in React.
- Keep the UI contract stable unless the user explicitly asks for a breaking change.

## Backend rules

- Do not use destructive git commands unless the user asks for them.
- Prefer `apply_patch` for manual edits.
- Keep changes ASCII unless the file already uses non-ASCII text.
- Preserve the current HTTP contract unless asked otherwise.
- If you touch the processing flow, verify the Metashape entry path and the
  project naming logic.

## When editing the map

- Keep the map container full screen.
- Keep Leaflet initialization in `frontend/src/App.jsx`.
- Keep the orthomosaic tile endpoint as the single source for the final RGB view.
- If you change map interactivity, verify both pan and scroll zoom.
- The control panel is a fixed overlay with `pointer-events: none`. Only
  `.card` children have `pointer-events: auto`. Do not add a global
  `.control-panel > * { pointer-events: auto }` rule — it blocks map
  interaction (zoom, pan) by capturing all events in empty layout areas.
- Leaflet native `scrollWheelZoom` handles zoom. Do not add a custom
  `wheel` document listener — it replaces smooth zoom with discrete steps
  and breaks the native behavior.

## Practical workflow for agents

1. Read the current file state first.
2. Make the smallest change that solves the issue.
3. Build the frontend with `corepack pnpm build`.
4. If backend files change, sanity check Python syntax.
5. Report the exact files touched.
