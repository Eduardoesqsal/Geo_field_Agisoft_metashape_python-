# Memoria de trabajo · GeoField motor local

Actualizado: 2026-11-10. Este archivo resume el estado para continuar el
desarrollo; el funcionamiento detallado está en [README.md](README.md).

## Objetivo

Usar esta PC como motor Metashape: recibir ZIP de fotos crudas enviados desde
la web GeoField, preparar trabajos en cola, procesar uno a la vez desde la UI
local y entregar el ortomosaico a la aplicación web. Trabajar en este
repositorio sin modificar el servicio de producción.

## Implementado

- Frontend React/Leaflet con mapa, proyectos, carga de ZIP, puntos GPS, ROI,
  tiles RGB, logs y tabla **Trabajos recibidos**.
- Backend FastAPI y flujo Metashape para Mavic 3 RGB, Mavic 3 MS y MicaSense
  RedEdge-M. Proyectos, datasets y resultados se guardan en carpetas locales.
- Cola SQLite en `incoming/jobs.sqlite3`: el receptor puede consultar lotes
  `received` y descargar ZIP de B2 mediante URLs autorizadas, con reanudación.
- Preparación manual por turno: extrae ZIP, crea dataset y proyecto, conserva
  el nombre del trabajo y permite elegir sensor. Metashape inicia solo al
  pulsar **Procesar proyecto**. El siguiente turno queda disponible al terminar.
- Simulación local con `scripts/simular_trabajo_local.py` probada con un ZIP
  real. No depende de las credenciales de producción.

## Pendiente

1. Completar `.env.receiver.local` con los cinco valores del backend de
   producción y probar una carga real. La plantilla y pasos están en
   [docs/RECEPTOR_TRABAJOS.md](docs/RECEPTOR_TRABAJOS.md). No guardar secretos
   en documentación ni Git.
2. Acordar con el desarrollador de producción el contrato para subir los
   GeoTIFF y publicar/mostrar resultados. El flujo de ZIP existente solo
   permite descargar insumos y cambiar el estado del lote. Ver
   [docs/ENTREGA_RESULTADOS.md](docs/ENTREGA_RESULTADOS.md).
3. Implementar y probar la entrega/reintento de resultados cuando exista ese
   contrato. `done` remoto por sí solo no publica el ortomosaico.

## Decisiones y límites observados

- El receptor previsto consulta producción desde esta PC; Funnel no es
  necesario para descargar ZIP. Exponer la UI por Funnel es otra función.
- La UI usa un estado activo compartido en memoria; aún no aísla sesiones de
  distintos usuarios. La cola limita Metashape a un trabajo activo.
- Mantener el orden de bandas propio de cada sensor y la exportación MS actual
  a 0.20 m al integrar nuevas funciones. El flujo RGB tiene su propia
  resolución. Evitar cambios colaterales en la fotogrametría al trabajar en
  integración o documentación.
- `dataset/`, `proyecto/`, `incoming/` y `logs/` contienen datos locales y
  están ignorados por Git. No borrarlos como parte de limpieza del código.
- La primera simulación creó una carpeta temporal con permisos inaccesibles;
  el script ya usa una carpeta con permisos heredados. Su entrada fallida fue
  retirada de la cola, pero la carpeta antigua no pudo borrarse por permisos
  de Windows. Los trabajos nuevos se pueden preparar normalmente.

## Puntos de entrada

- `python app.py` después de `npm run build` en `frontend/`.
- `backend/routes.py` para API; `backend/services/` para proyectos, cola e
  ingesta; `backend/workflow.py` para el procesamiento Metashape.
- `frontend/src/App.jsx` y `frontend/src/styles.css` para interfaz.
- `python -m unittest discover -s tests` y `npm run build` para verificación.
