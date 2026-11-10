# Receptor local de trabajos de GeoField

Esta PC consulta los lotes terminados de GeoField por HTTPS, descarga los ZIP
a `incoming/<organizacion>/<lote>/` y guarda una cola en `incoming/jobs.sqlite3`.
La pantalla local muestra cada trabajo con su nombre, organización, cantidad de
ZIP y estado. El receptor no cambia el estado del lote remoto.

## Configuración

La plantilla versionada es `receptor.env.example`. En esta PC ya existe
`.env.receiver.local` con los nombres de variables vacíos para que el
desarrollador complete los valores. El archivo local está ignorado por Git.
En otra instalación, copia la plantilla a `.env.receiver.local`.

Primero valida la configuración sin iniciar el servidor:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\iniciar_receptor.ps1 -ValidateOnly
```

Después, si el puerto 8001 ya está ocupado, detén el servidor anterior e inicia
el receptor con:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\iniciar_receptor.ps1
```

El script carga los valores solo en ese proceso; no cambia las variables
permanentes de Windows ni imprime las credenciales.

Configura estas variables de entorno en la sesión o servicio de Windows que
ejecuta `python app.py`:

```text
GEOFIELD_API_BASE_URL=https://tu-backend.example.com/api
GEOFIELD_SUPABASE_URL=https://tu-proyecto.supabase.co
GEOFIELD_SUPABASE_ANON_KEY=<clave pública de Supabase>
GEOFIELD_WORKER_EMAIL=<correo de la cuenta del receptor>
GEOFIELD_WORKER_PASSWORD=<contraseña de esa cuenta>
GEOFIELD_POLL_SECONDS=30
```

La cuenta debe tener permiso `can_download_raw_uploads` o ser administradora de
plataforma y tener habilitado el acceso a las rutas de procesamiento. El
receptor obtiene y renueva su JWT mediante Supabase Auth. En esta instalación,
deja `GEOFIELD_ACCESS_TOKEN` vacío: no se pedirá un token manual. El código
admite ese token solo como alternativa temporal, pues caduca y el receptor
dejaría de consultar cuando expire.
No guardes contraseñas ni tokens en Git.

Sin `GEOFIELD_API_BASE_URL`, `python app.py` arranca normalmente y la cola queda
sin conexión. El script de arriba, en cambio, avisa cuál variable falta. La
configuración no modifica la aplicación de producción.

## Comprobación

### Prueba local sin credenciales de producción

Con el motor ejecutándose en `http://127.0.0.1:8001`, usa un ZIP existente con
fotos reales del vuelo. Desde la raíz de este proyecto, ejecuta:

```powershell
python .\scripts\simular_trabajo_local.py --nombre "Prueba de vuelo" "C:\ruta\a\fotos.zip"
```

El comando copia el ZIP a `incoming/prueba_local/` y crea un trabajo **En espera**
en la misma cola que usará el receptor. No consulta producción ni ejecuta
Metashape. El ZIP original permanece en su sitio.

1. Si hay otro proyecto abierto en la interfaz, guárdalo y ciérralo.
2. Abre **Cola de trabajos**. Aparecerá **Prueba de vuelo**; selecciona el sensor
   y pulsa **Preparar**.
3. Espera a que termine la extracción. Revisa el número de fotos, los puntos
   GPS y el ROI, si corresponde.
4. Pulsa **Procesar proyecto** y sigue el avance en **Logs**. Al terminar,
   guarda o cierra el proyecto para habilitar el siguiente trabajo.

Esta prueba cubre la cola, la preparación y el procesamiento local. Para
comprobar la descarga automática desde la web se necesitan las variables de
producción indicadas arriba y un lote terminado en esa web.

### Prueba de la descarga real

1. Inicia el motor con `iniciar_receptor.ps1` y abre `http://127.0.0.1:8001`.
   `GET /trabajos` debe indicar `enabled: true` y `running: true`.
2. Sube y termina un ZIP pequeño desde el formulario existente de GeoField.
3. Comprueba **Cola de trabajos** en la interfaz local o `GET /trabajos`.
4. Al quedar **En espera**, comprueba que `GET
   /trabajos/{organization_id}/{batch_id}/archivos` enumera el ZIP y que el
   archivo existe en `incoming/`.

El receptor solo toma lotes con estado `received` y `completedAt`. Si la PC se
apaga durante una descarga, conserva el archivo `.part` y solicita el tramo
restante en el siguiente intento. Comprueba el tamaño y que el archivo sea un
ZIP antes de marcarlo **En espera**. Usa `(organization_id, batch_id)` como clave
única para no descargar de nuevo un lote ya recibido.

**En espera** significa que los ZIP llegaron íntegros a esta PC. En la tabla
local, el primer trabajo en espera puede seleccionar sensor y pulsar
**Preparar**. La preparación extrae sus ZIP a un dataset propio, conserva el
nombre `jobCode` como nombre visible y crea un proyecto local. Los ZIP originales
permanecen en `incoming/`. Si el nombre ya existe, el identificador de carpeta
recibe un sufijo para evitar sobrescribir otro proyecto.

Después de preparar, la interfaz muestra el número de fotos y sus puntos GPS.
El operador puede importar o dibujar un ROI y pulsar el botón existente
**Procesar proyecto**. Solo Metashape procesa el trabajo; la preparación no lo
inicia automáticamente. Mientras un trabajo está preparado o procesándose, el
siguiente lote no puede tomar el turno. Al terminar, el estado local pasa a
**Terminado** o **Falló**. El siguiente lote queda habilitado cuando se guarda
o cierra el proyecto actual.

Esta etapa no publica el ortomosaico en producción ni actualiza el estado
`processing`/`done` del backend remoto. Esa entrega requiere un contrato aparte
con el destino actual de MapTiler.

## Datos pendientes para el desarrollador de producción

- Colocar la URL base real de FastAPI, terminada en `/api`.
- Colocar `GEOFIELD_SUPABASE_URL`, `GEOFIELD_SUPABASE_ANON_KEY` y los datos de
  una cuenta con permiso `can_download_raw_uploads` (o administradora). El
  receptor obtiene y renueva el token automáticamente; no hace falta pedir al
  usuario que copie un access token. La URL y clave pública de Supabase deben
  obtenerse de la configuración del backend o frontend de producción.
- Terminar un lote ZIP pequeño desde la pantalla de producción y confirmar que
  se descargue a `incoming/` y figure **En espera** en `/trabajos`.

La subida del navegador ya va directamente a Backblaze B2. El receptor descarga
el lote desde B2 mediante URLs autorizadas del backend; Funnel no interviene en
ese transporte. La publicación del ortomosaico es una etapa posterior.
