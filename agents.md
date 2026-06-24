# Project Notes for Agents

## What this repo is

This repository is a FastAPI backend plus a React frontend for a Metashape
processing workflow.

Current scope:

- load imagery from `dataset/`, ZIP, or Google Drive
- run Agisoft Metashape in the background
- export RGB and multispectral outputs to `proyecto/`
- serve an interactive web UI from `frontend/`
- render the final RGB ortho as tiled overlay
- allow starting a fresh project after a finished run

## Current architecture

- `backend/runtime.py`
  - shared state, logs, cache, project reset helpers, frontend serving helpers
- `backend/services/ingestion.py`
  - ZIP/Drive ingestion and dataset extraction
- `backend/services/process.py`
  - process launch, stop, and completion tracking
- `backend/services/overlay.py`
  - ortho export discovery, overlay PNG generation, tile serving
- `backend/routes.py`
  - HTTP endpoints
- `backend/config.py`
  - runtime paths and project filenames
- `backend/workflow.py`
  - Metashape processing pipeline
- `backend/main.py`
  - Metashape entry script executed by the worker process
- `frontend/src/App.jsx`
  - React UI and map integration
- `frontend/src/styles.css`
  - all UI styling
- `app.py`
  - Python entry point that serves the app

## Frontend rules

- Use `pnpm` through `corepack` for frontend commands.
- Run installs with:
  - `corepack pnpm install`
- Build with:
  - `corepack pnpm build`
- Dev server with:
  - `corepack pnpm dev`
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

## Important endpoints

- `GET /status`
- `GET /logs`
- `GET /ingesta/estado`
- `POST /ingesta/zip`
- `POST /ingesta/drive`
- `POST /proyecto/nombre`
- `POST /proyecto/nuevo`
- `POST /procesar`
- `POST /start`
- `POST /stop`
- `GET /overlay/status`
- `GET /overlay/rgb.png`
- `GET /tiles/rgb/{z}/{x}/{y}.png`

## Current UI behavior

- The app is a React full-screen map with floating cards.
- The base map is Bing Aerial.
- The final RGB ortho is shown as a single overlay layer.
- The control panel is visually separate from the map.
- The UI includes:
  - project name
  - camera model
  - upload ZIP
  - Drive ingest
  - process start/stop
  - logs
  - summary
  - project reset

## Project reset flow

- `POST /proyecto/nuevo` resets runtime state for a new project.
- The frontend exposes a `Nuevo proyecto` action.
- Resetting clears runtime state and project outputs from `proyecto/`.
- Do not remove dataset imagery unless the user explicitly requests it.

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

## Practical workflow

1. Read the current file state first.
2. Make the smallest change that solves the issue.
3. Build the frontend with `corepack pnpm build`.
4. If backend files change, sanity check Python syntax.
5. Report the exact files touched.
