# Receptor local de trabajos de GeoField

Esta PC consulta los lotes terminados de GeoField por HTTPS, descarga los ZIP
a `incoming/<organizacion>/<lote>/` y guarda una cola en `incoming/jobs.sqlite3`.
La pantalla local muestra los estados **Nuevo**, **Descargando**, **En espera** y
**Error**. El receptor no inicia Metashape ni cambia el estado del lote remoto;
esas son etapas posteriores de la integración.

## Configuración

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
receptor obtiene y renueva su JWT mediante Supabase Auth. Para una prueba corta
se puede usar `GEOFIELD_ACCESS_TOKEN` en lugar de las cuatro variables de
Supabase; ese token caduca y el receptor dejará de consultar cuando expire.
No guardes contraseñas ni tokens en Git.

Sin `GEOFIELD_API_BASE_URL`, la aplicación local arranca normalmente y la cola
queda sin conexión. La configuración no modifica la aplicación de producción.

## Comprobación

1. Inicia `python app.py` y abre `http://127.0.0.1:8001`.
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

En esta primera etapa, **En espera** significa que el ZIP llegó íntegro a esta
PC. Todavía no se extraen las fotos ni se crea un proyecto de Metashape.
