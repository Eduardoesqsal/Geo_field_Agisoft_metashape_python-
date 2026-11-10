# Flujo de «Procesamiento»: carga de ZIP de fotos crudas

Este documento describe el comportamiento implementado en este repositorio para la pantalla **Procesamiento → Nueva carga → Cargas de tu organización**. Está pensado para entregar a un agente de desarrollo. La descripción de la interfaz proviene del texto facilitado; este repositorio contiene el backend, no el código de esa pantalla. Por ello se distingue entre lo que hace el backend y lo que debe hacer el cliente.

## Resumen funcional

1. Un usuario autorizado declara un trabajo (`jobCode`) y uno o varios archivos `.zip`. El backend crea un **lote** con un manifiesto y devuelve los identificadores de lote y archivo.
2. El navegador pide enlaces temporales y envía los bytes **directamente a Backblaze B2**. La API de FastAPI gestiona permisos, metadatos y enlaces; no recibe el ZIP.
3. Para archivos grandes, la carga se divide en partes. B2 conserva las partes recibidas y el backend las consulta para permitir continuar una carga interrumpida.
4. Tras subir todos los archivos, el cliente llama a `finish`. El backend comprueba en B2 la presencia y el tamaño exacto de cada archivo y registra el lote como `received`.
5. Personal autorizado puede descargar los ZIP y marcar el lote como `processing` o `done`. **Este flujo no ejecuta fotogrametría, no extrae fotos, no crea ortomosaicos y no dispara un trabajo automático de procesamiento.** Es una entrega de insumos para que el equipo los procese fuera de este flujo.

## Componentes y persistencia

| Componente | Responsabilidad |
| --- | --- |
| `app/api/raw_uploads.py` | Rutas REST, autenticación, organización, permisos y respuestas. |
| `app/models/schemas.py` | Validación de solicitud y contratos de respuesta. |
| `app/services/raw_upload_service.py` | Manifiesto, enlaces B2, multipart, verificación, estados y descarga. |
| `app/services/b2_storage.py` | Cliente S3 compatible de B2 y configuración del bucket. |
| `app/services/raw_upload_index.py` | Índice paginado de historial en PostgreSQL; sincroniza resúmenes con las operaciones en B2. |
| `app/api/hectares.py` y `app/services/hectare_service.py` | Saldo de hectáreas separado del transporte de ZIP. |

Los objetos privados de B2 se organizan así:

```text
uploads/{organization_id}/{batch_id}/_manifest.json
uploads/{organization_id}/{batch_id}/files/{file_id}.zip
uploads/{organization_id}/{batch_id}/_complete.json
uploads/{organization_id}/{batch_id}/_status.json
```

`_manifest.json` se escribe al crear el lote y contiene `batchId`, `organizationId`, `jobCode`, creador, fecha, límites de partes, total de bytes y cada archivo (`fileId`, nombre, `relativePath`, tamaño y clave B2). Los ID y claves de almacenamiento los genera el servidor. `relativePath` es metadato de presentación, no una clave elegida por el navegador. `_complete.json` aparece al finalizar y `_status.json` cuando el personal actualiza el estado. La tabla PostgreSQL `raw_upload_batches` guarda un resumen para búsquedas/listados; el manifiesto y los objetos B2 siguen siendo la referencia para operar sobre archivos.

## Acceso y alcance

- Las rutas `/api/raw-uploads/*` están detrás de `require_processing_beta`. Administradores de plataforma pasan siempre; los demás usuarios necesitan estar en `PROCESSING_BETA_EMAILS` (por defecto un correo configurado en `app/config.py`; `*` habilita todos los correos). Sin acceso a la función se responde `404 FEATURE_NOT_AVAILABLE`.
- `GET /api/raw-uploads/access` informa `configured`, `canUpload`, `canDownload` y `canDelete`. Sin las credenciales de B2, las operaciones que necesitan almacenamiento responden `503 RAW_UPLOADS_NOT_CONFIGURED`.
- Para crear/subir/finalizar se requiere administrador de plataforma o `users.can_upload_raw_uploads`, además de acceso efectivo a la organización. El `organization_id` opcional de la consulta se resuelve y valida en el servidor.
- Los miembros con acceso efectivo a su organización pueden consultar sus lotes y descargar los lotes terminados. En la vista cliente `createdByEmail` se oculta.
- Las rutas `/manage` son para administradores de plataforma, usuarios con `can_download_raw_uploads` o responsables de cuentas en las organizaciones que administran. El permiso de descarga habilita alcance global; un responsable sin ese permiso queda limitado a sus organizaciones. Pueden listar, descargar y actualizar estado. Solo administradores de plataforma pueden eliminar lotes.

## Contrato de carga, paso a paso

Todas las rutas siguientes llevan prefijo `/api`. La autenticación es la sesión normal del backend; las URLs prefirmadas se usan directamente contra B2.

### 1. Crear el lote

`POST /raw-uploads/batches` con cuerpo de ejemplo:

```json
{
  "jobCode": "Rancho_2026_10_23",
  "files": [
    {"name": "vuelo.zip", "relativePath": "vuelo.zip", "size": 123456789}
  ]
}
```

Devuelve `201` y un detalle con `batchId`, `organizationId`, `jobCode`, `status: "uploading"`, límites de carga, lista de archivos con `fileId` y `uploadedFileIds`. **Crear el lote no sube bytes**. Acepta de 1 a 5,000 archivos, únicamente extensión `.zip` sin importar mayúsculas, nombres de 1–255 caracteres, rutas relativas de 1–1,024 caracteres y tamaños positivos. `jobCode` se recorta y debe tener entre 1 y 100 caracteres. Las rutas relativas repetidas, segmentos `.` o `..`, caracteres de control y separadores dentro de `name` se rechazan. Cada archivo puede medir como máximo 32 MiB × 10,000 partes; el lote completo, 2 TiB.

### 2A. Archivo de hasta 64 MiB

`POST /raw-uploads/batches/{batchId}/sign` con `{"fileIds":["..."]}` devuelve `{"urls":{"fileId":"https://..."}}`. El cliente realiza un `PUT` del archivo completo a esa URL de B2. Se pueden solicitar hasta 100 ID por llamada. El enlace dura 30 minutos. Para reanudar un archivo de este tipo, se consulta el detalle del lote: si su ID está en `uploadedFileIds`, ya está completo; en caso contrario se vuelve a enviar el archivo entero. No hay reanudación por bytes dentro de un PUT único.

### 2B. Archivo de más de 64 MiB

1. `POST /raw-uploads/batches/{batchId}/files/{fileId}/multipart` devuelve `uploadId`, `partSize` (32 MiB), `partCount`, `uploadedParts` y `alreadyUploaded`. Reutiliza la carga multipart abierta más reciente para esa clave B2; si el objeto final ya existe con el tamaño esperado, marca `alreadyUploaded: true`.
2. Para las partes faltantes, `POST /raw-uploads/batches/{batchId}/files/{fileId}/multipart/sign` con `{"uploadId":"...","partNumbers":[1,2]}` devuelve URL por número de parte. Se admiten hasta 100 partes por solicitud. El cliente hace un `PUT` de cada segmento a B2; el último puede ser menor de 32 MiB. Los enlaces duran 30 minutos.
3. `POST /raw-uploads/batches/{batchId}/files/{fileId}/multipart/complete` con `{"uploadId":"..."}` devuelve `204` tras comprobar en B2 que existen todas las partes con los tamaños previstos, reunir sus ETag del propio B2 y completar el objeto. Si faltan partes responde `409 RAW_UPLOAD_PARTS_MISSING`; si se perdió la sesión y el objeto no existe, `409 RAW_UPLOAD_SESSION_EXPIRED`. Una respuesta perdida tras completar es tolerada si el objeto ya existe con el tamaño correcto.

El backend ofrece las piezas necesarias para continuar después de una interrupción. La detección de reconexión, la nueva selección del archivo al cerrar la página y la concurrencia de seis partes indicada en la documentación existente corresponden al cliente web; **este repositorio no contiene ese cliente**, así que no se puede verificar aquí su implementación exacta. El backend identifica la carga pendiente mediante lote, archivo, clave B2 y partes presentes, sin comparar un hash del contenido. Conviene conservar `batchId` y los metadatos locales y volver a elegir el mismo ZIP al regresar.

### 3. Consultar y terminar

`GET /raw-uploads/batches/{batchId}` devuelve el manifiesto público y `uploadedFileIds`, calculados comparando tamaño de cada objeto en B2 con el tamaño declarado. `POST /raw-uploads/batches/{batchId}/finish` verifica **todos** los archivos del manifiesto. Si falta alguno o tiene otro tamaño, responde `409 RAW_UPLOAD_FILES_MISSING` con hasta 20 rutas faltantes. Si todo coincide, escribe `_complete.json`, intenta cancelar multipart sobrantes y cambia el estado derivado a `received`. Repetir `finish` en un lote ya completo devuelve su resumen. Después de terminar, las rutas para firmar o iniciar nuevas cargas rechazan el lote con `409 RAW_UPLOAD_BATCH_COMPLETE`.

La verificación es de existencia y tamaño; no comprueba integridad criptográfica, estructura interna del ZIP, fotos, metadatos de vuelo ni superficie del terreno.

## Historial y estados

`GET /raw-uploads/batches?limit=50&offset=0` lista los lotes de la organización de más nuevo a más antiguo. Con `limit` devuelve `{"items":[...],"total":N}`; se permiten 1–100 resultados por página. Si se omite `limit`, conserva la respuesta antigua como arreglo completo. El listado lee PostgreSQL, no B2. `GET /raw-uploads/manage/batches` agrega búsqueda por organización, trabajo o remitente y filtro de estado para el personal. Si el índice histórico no ha terminado de importarse, el listado devuelve `503 RAW_UPLOAD_INDEX_NOT_READY`; el detalle de un lote sigue disponible.

| Estado API | Significado | Quién lo establece |
| --- | --- | --- |
| `uploading` | Existe manifiesto, falta la confirmación final del cliente. | Derivado de ausencia de `_complete.json`. |
| `received` | Todos los archivos pasaron la comprobación de tamaño y se creó `_complete.json`. | `finish`, o actualización posterior del personal. |
| `processing` | El personal declaró que está procesando el trabajo. | Actualización manual de estado. |
| `done` | El personal declaró el trabajo finalizado. | Actualización manual de estado. |

El personal usa `PUT /raw-uploads/manage/batches/{orgId}/{batchId}/status` con `{"status":"processing"}` (también `received` o `done`). Solo admite lotes terminados; registra quién y cuándo en `_status.json`. Hay variante masiva `POST /raw-uploads/manage/batches/status`, hasta 100 lotes, con resultados `succeeded` y `failed`. El backend **no exige la secuencia** `received → processing → done`: un operador autorizado puede fijar cualquiera de los tres estados admitidos en un lote terminado.

## Descargas y cierre operativo

`POST /raw-uploads/batches/{batchId}/download-urls` permite a un miembro de la organización obtener enlaces GET de sus archivos; `/raw-uploads/manage/batches/{orgId}/{batchId}/download-urls` sirve al personal en su alcance. Ambos requieren `_complete.json`, aceptan `{"fileIds":["..."]}` y devuelven URLs prefirmadas por ID (máximo 100, duración 30 minutos). La descarga va de B2 al navegador, con el nombre original indicado en `Content-Disposition`. El estado `done` **no** representa un archivo de resultado publicado aquí; solo registra el estado del lote de insumos.

La eliminación, cuando se necesita, usa `POST /raw-uploads/manage/batches/delete`; aborta multipart abiertos, elimina versiones de objetos y quita el resumen del índice. Es una operación administrativa independiente de la carga normal.

## «Hectáreas disponibles»

La cifra de la pantalla proviene del subsistema de saldo por organización, no del tamaño del ZIP. `GET /api/hectares/organizations/{organization_id}` devuelve saldo y movimientos bajo sus propias reglas de acceso. Los planes, recargas, ajustes y consumos se registran como movimientos; `POST /api/hectares/organizations/{organization_id}/movements` permite que personal autorizado registre consumo. Este flujo de ZIP **no consulta el saldo antes de crear o terminar el lote, no calcula hectáreas a partir de fotos y no descuenta hectáreas automáticamente**. El saldo puede ser negativo según `hectare_service.py`. Así, «1,500 ha» es una lectura de un saldo ya existente, no una reserva ni un límite aplicado al archivo que se está subiendo.

## Límites, errores y puntos de atención para desarrollo

- `503 RAW_UPLOADS_NOT_CONFIGURED`: faltan `B2_ENDPOINT_URL`, `B2_KEY_ID`, `B2_APPLICATION_KEY` o `B2_BUCKET`. B2 debe permitir CORS desde los orígenes del navegador para PUT/GET directos.
- `403 RAW_UPLOAD_UPLOAD_DENIED`: el usuario no tiene permiso de carga. El alcance de organización se valida adicionalmente.
- `422`: solicitudes inválidas, extensión distinta de ZIP, límites de archivos/tamaño, o uso de PUT simple cuando requiere multipart (y viceversa).
- `409 RAW_UPLOAD_FILES_MISSING`, `RAW_UPLOAD_PARTS_MISSING`, `RAW_UPLOAD_SESSION_EXPIRED` o `RAW_UPLOAD_BATCH_COMPLETE`: gestionar reintentos y mostrar el estado recuperable al usuario.
- Los enlaces PUT emitidos antes de `finish` pueden seguir vigentes hasta 30 minutos; el marcador de finalización impide pedir nuevos enlaces, pero no revoca los anteriores.
- La recuperación multipart se basa en la clave y los tamaños de partes que B2 informa. No hay checksum de extremo a extremo ni comparación del contenido al reanudar: no sustituir un ZIP por otro con el mismo nombre/tamaño para un lote en curso.
- B2 y PostgreSQL no comparten transacción. Si una escritura B2 termina y falla la actualización del índice, existe la utilidad `python -m scripts.backfill_raw_upload_history --repair ORG_ID BATCH_ID`; el despliegue inicial del índice requiere `python -m scripts.backfill_raw_upload_history --max-pages 10` hasta completar. La documentación preexistente indica que ese despliegue aún no se había aplicado a producción en el momento en que se escribió; este análisis del repositorio no verifica el estado actual del entorno.

## Flujo separado que no corresponde a esta pantalla

También existen `POST /api/flights/{flight_id}/drone-upload` y `vm-processing/index.js`: entregan una URL TUS de una VM y un token para otro flujo asociado a vuelos. La VM actual registra `handleUploadComplete` como **placeholder** para una futura integración con Metashape. Esa ruta no es el flujo de lotes `/api/raw-uploads/*` descrito aquí, que utiliza B2.

## Referencias del código

- `app/api/raw_uploads.py`; `app/services/raw_upload_service.py`; `app/services/raw_upload_index.py`; `app/services/b2_storage.py`.
- `app/models/schemas.py` (sección «Raw drone image uploads»); `app/models/raw_upload.py`; `app/utils/feature_flags.py`; `app/main.py`.
- `app/api/hectares.py`; `app/services/hectare_service.py`; `app/api/flights.py`; `vm-processing/index.js`.
- `docs/raw-uploads/README.md` contiene antecedentes operativos y configuración de B2.
