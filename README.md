# Procesamiento Metashape + React

Aplicacion para procesar imagenes con Agisoft Metashape desde un backend
FastAPI y visualizar el resultado en una interfaz React a pantalla completa.

## Que hace

- recibe imagenes por carga directa, ZIP o Google Drive
- ejecuta Metashape en segundo plano
- exporta ortomosaico RGB y salida multiespectral
- publica el ortomosaico RGB como overlay por tiles
- muestra logs, estado, resumen y controles desde una UI React
- permite reiniciar el flujo para crear un nuevo proyecto

## Estado actual

La aplicacion ya esta organizada en dos partes:

- `backend/` para FastAPI, workflow y servicios
- `frontend/` para la UI React

La interfaz web:

- usa un mapa base satelital
- muestra el ortomosaico final sobre el mapa
- permite trabajar con cards flotantes
- incluye acciones para subir datos, procesar y crear un nuevo proyecto

## Estructura

- `app.py`
  - arranque principal del backend
- `backend/`
  - API, runtime, servicios y workflow de Metashape
- `frontend/`
  - React + Vite + estilos de la UI
- `dataset/`
  - entrada de imagenes
- `proyecto/`
  - proyecto Metashape y ortomosaicos exportados
- `logs/`
  - registros del proceso

## Requisitos

- Windows
- Python instalado
- Agisoft Metashape Pro instalado
- Node.js con soporte para `corepack`
- Dependencias del backend y frontend instaladas

## Instalacion

### Backend

Instala dependencias Python segun tu entorno actual del proyecto.

### Frontend

```bash
cd frontend
corepack pnpm install
corepack pnpm build
```

## Como correr la app

1. Compila el frontend:

```bash
cd frontend
corepack pnpm build
```

2. Ejecuta el backend desde la raiz del proyecto:

```bash
python app.py
```

3. Abre la URL local que imprima el servidor.

## Docker y Render

El proyecto incluye un `Dockerfile` en la raiz para desplegar el backend y el
frontend compilado en un solo contenedor.

En Render puedes usar un Web Service basado en Docker. El servicio arranca la
API y sirve la UI desde `frontend/dist/`.

Notas importantes:

- Render define `PORT` automaticamente para el contenedor.
- La ruta de Metashape se puede ajustar con la variable `METASHAPE_EXE`.
- Si `METASHAPE_EXE` no apunta a un ejecutable valido, la app arranca pero el
  endpoint de procesado devolvera error al intentar iniciar Metashape.
- GitHub Actions puede disparar el deploy con un `RENDER_DEPLOY_HOOK_URL`
  guardado como secret.

## Flujo de trabajo

1. Cargas imagenes en `dataset/` o por la UI.
2. Guardas o cambias el nombre del proyecto.
3. Inicias el procesamiento.
4. Metashape genera el ortomosaico.
5. El backend publica el raster como overlay/tile layer.
6. La UI cambia al ortomosaico final.
7. Si quieres otro proyecto, usas `Nuevo proyecto`.

## Funciones clave

- `POST /ingesta/zip`
- `POST /ingesta/drive`
- `POST /proyecto/nombre`
- `POST /proyecto/nuevo`
- `POST /procesar`
- `POST /start`
- `POST /stop`
- `GET /status`
- `GET /logs`
- `GET /overlay/status`
- `GET /overlay/rgb.png`
- `GET /tiles/rgb/{z}/{x}/{y}.png`

## Ingesta

La app acepta:

- solo JPG
- solo TIFF
- JPG + TIFF
- datos multiespectrales RedEdge-M

## Salidas

El backend trabaja con:

- `proyecto/*.psx`
- `proyecto/*_rgb.tif`
- `proyecto/*_ms.tif`
- tiles servidos desde el raster RGB exportado

## UI

La UI React incluye:

- mapa base Bing Aerial con scroll zoom nativo de Leaflet
- cards flotantes sobre el mapa
- logs
- resumen
- controles de proyecto
- selector de modelo de camara
- carga de archivos
- boton de nuevo proyecto

### Interaccion con el mapa

El panel de control se superpone al mapa pero solo las cards capturan
eventos de puntero (`.card { pointer-events: auto }`). Las areas vacias del
layout dejan pasar clics y scroll al mapa, permitiendo pan y zoom natural.
El scroll zoom usa el handler nativo de Leaflet (`scrollWheelZoom: true`).
El nivel maximo de zoom es 24; los tiles de Bing tienen `maxNativeZoom: 19`.

## Nuevo proyecto

Cuando un procesamiento termina, puedes reiniciar el flujo con `Nuevo proyecto`.
Ese paso limpia el estado de runtime y prepara la app para volver a procesar
otro conjunto de imagenes.

## Notas de implementacion

- El backend mantiene el estado del proceso en memoria.
- El overlay final se genera desde el ortomosaico RGB exportado.
- La UI no replica la logica de procesamiento, solo consume los endpoints.
- Si tocas el mapa, valida pan, zoom y overlay final.

## Comandos utiles

```bash
cd frontend
corepack pnpm install
corepack pnpm build
```

```bash
python app.py
```

## Archivos importantes

- [app.py](./app.py)
- [backend/runtime.py](./backend/runtime.py)
- [backend/routes.py](./backend/routes.py)
- [backend/services/process.py](./backend/services/process.py)
- [backend/services/overlay.py](./backend/services/overlay.py)
- [backend/workflow.py](./backend/workflow.py)
- [frontend/src/App.jsx](./frontend/src/App.jsx)
- [frontend/src/styles.css](./frontend/src/styles.css)

## Resumen rapido

Esta app es un sistema de procesamiento fotogrametrico con Metashape, backend
FastAPI y frontend React. El objetivo principal es cargar imagenes, procesarlas,
publicar el ortomosaico y permitir reiniciar el flujo para nuevos proyectos sin
reconstruir la aplicacion.
