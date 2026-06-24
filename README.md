# Procesamiento Metashape + React

Aplicación para procesar imágenes con Agisoft Metashape desde un backend
FastAPI y visualizar el resultado en una interfaz React a pantalla completa.

## Qué hace

- Recibe imágenes por carga directa, ZIP o Google Drive
- Ejecuta Metashape en segundo plano
- Exporta ortomosaico RGB y salida multiespectral
- Publica el ortomosaico RGB como overlay por tiles (Leaflet)
- Muestra logs, estado, resumen y controles desde una UI React
- Permite reiniciar el flujo para crear un nuevo proyecto

## Estructura del proyecto

```
.
├── app.py                  # Arranque del backend (uvicorn)
├── backend/
│   ├── application.py      # Factory de FastAPI
│   ├── config.py           # Rutas y nombres de proyecto
│   ├── main.py             # Script de Metashape (worker)
│   ├── routes.py           # Endpoints HTTP
│   ├── runtime.py          # Estado compartido, logs, reset
│   ├── workflow.py         # Pipeline de Metashape
│   └── services/
│       ├── ingestion.py    # Carga ZIP / Google Drive
│       ├── overlay.py      # Generación de tiles del ortomosaico
│       └── process.py      # Lanzar/detener proceso Metashape
├── frontend/
│   ├── src/
│   │   ├── App.jsx         # UI principal + mapa Leaflet
│   │   ├── main.jsx        # Entry point React
│   │   └── styles.css      # Estilos
│   ├── package.json
│   ├── pnpm-lock.yaml
│   └── vite.config.js
├── dataset/                # Imágenes de entrada (ignorado por git)
├── datasets_crudos/        # Datos crudos grandes (ignorado por git)
├── proyecto/               # Salidas .psx, .tif, tiles (ignorado por git)
├── logs/                   # Registros de proceso (ignorado por git)
├── Dockerfile              # Build multi-etapa para producción
├── .github/workflows/
│   └── ci-cd.yml           # Pipeline CI/CD (GitHub Actions)
└── requirements.txt        # Dependencias Python
```

## Requisitos

### Local (desarrollo con Metashape)
- Windows
- Python 3.11+
- Agisoft Metashape Pro instalado
- Node.js 24+ con corepack habilitado
- pnpm (se activa vía corepack)

### Solo contenedor (sin Metashape)
- Docker
- La API y el frontend funcionan, pero el procesamiento Metashape no estará disponible

## Instalación y ejecución local

### Backend

```bash
pip install -r requirements.txt
python app.py
```

### Frontend

```bash
cd frontend
corepack enable
corepack pnpm install
corepack pnpm build
```

O para desarrollo con hot-reload:

```bash
corepack pnpm dev
```

## Docker

### Construir imagen

```bash
docker build -t geo-metashape .
```

### Ejecutar contenedor

```bash
docker run -p 10000:10000 geo-metashape
```

La app estará disponible en `http://localhost:10000`.

### Variables de entorno

| Variable | Descripción | Default |
|---|---|---|
| `PORT` | Puerto del servidor | `10000` |
| `METASHAPE_EXE` | Ruta al ejecutable de Metashape | `C:\Program Files\Agisoft\Metashape Pro\metashape.exe` |
| `METASHAPE_PROJECT_NAME` | Nombre del proyecto | `test_agisoft` |

## CI/CD (GitHub Actions)

El pipeline en `.github/workflows/ci-cd.yml` se ejecuta en cada push a `main` y pull request:

| Job | Descripción |
|---|---|
| `frontend` | Compila el frontend con Node 24 + pnpm |
| `backend` | Verifica sintaxis de Python con `compileall` |
| `docker` | Construye la imagen Docker (depende de frontend + backend) |
| `deploy` | Dispara deploy en Render vía webhook (solo en push a `main`) |

### Secretos de GitHub requeridos

Para activar el deploy automático a Render:

1. En Render: Dashboard → Web Service → Settings → Deploy Hook → copiar URL
2. En GitHub: repo → Settings → Secrets and variables → Actions → New repository secret
3. **Nombre**: `RENDER_DEPLOY_HOOK_URL`
4. **Valor**: pegar la URL del hook de Render

## Despliegue en Render

1. Crea un Web Service en https://dashboard.render.com
2. Conecta tu repositorio de GitHub
3. Configura:

| Campo | Valor |
|---|---|
| **Name** | `geo-agisoft-metashape` |
| **Runtime** | Docker |
| **Branch** | `main` |
| **Root Directory** | (vacío) |
| **Port** | `10000` |

4. Agrega el secreto `RENDER_DEPLOY_HOOK_URL` en GitHub
5. Cada push a `main` construye y despliega automáticamente

> **Nota:** Render no tiene GPU ni Agisoft Metashape. La API y el frontend funcionarán, pero el procesamiento de imágenes requiere una máquina con Metashape (Windows + GPU).

## Endpoints de la API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/status` | Estado del proceso |
| `GET` | `/logs` | Logs del proceso |
| `GET` | `/ingesta/estado` | Estado de la ingesta |
| `POST` | `/ingesta/zip` | Subir imágenes en ZIP |
| `POST` | `/ingesta/drive` | Importar desde Google Drive |
| `POST` | `/proyecto/nombre` | Cambiar nombre del proyecto |
| `POST` | `/proyecto/nuevo` | Resetear proyecto |
| `POST` | `/procesar` | Iniciar procesamiento |
| `POST` | `/start` | Iniciar procesamiento |
| `POST` | `/stop` | Detener procesamiento |
| `GET` | `/overlay/status` | Estado del overlay |
| `GET` | `/overlay/rgb.png` | PNG del ortomosaico RGB |
| `GET` | `/tiles/rgb/{z}/{x}/{y}.png` | Tiles del ortomosaico |

## Formatos de entrada aceptados

- Solo JPG
- Solo TIFF
- JPG + TIFF
- Datos multiespectrales RedEdge-M (MicaSense)

## Flujo de trabajo

1. Carga imágenes en `dataset/` o por la UI (ZIP / Google Drive)
2. Define o cambia el nombre del proyecto
3. Selecciona el modelo de cámara
4. Inicia el procesamiento
5. Metashape genera el ortomosaico
6. El backend publica el raster como overlay de tiles
7. La UI muestra el ortomosaico final sobre el mapa
8. Para un nuevo proyecto, usa `Nuevo proyecto`

## UI React

- Mapa base Bing Aerial con scroll zoom nativo de Leaflet
- Cards flotantes sobre el mapa (controles, logs, resumen)
- Selector de modelo de cámara
- Carga de archivos (ZIP)
- Botón de nuevo proyecto
- Panel de control con `pointer-events: none` (solo las cards capturan eventos)
