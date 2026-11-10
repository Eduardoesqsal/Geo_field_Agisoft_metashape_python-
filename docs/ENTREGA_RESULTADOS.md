# Entrega de ortomosaicos a GeoField

## Estado actual

El motor guarda los resultados de cada proyecto en `proyecto/<project_id>/`.
Según el sensor y el vuelo, puede producir `<project_id>_rgb.tif`,
`<project_id>_ms.tif`, DEM, recortes por polígono y el archivo de Metashape
`<project_id>.psx`. El recorte solicitado se aplica al TIFF final dentro de esa
misma carpeta. Los lotes recibidos conservan en la cola local la asociación
`organization_id + batch_id -> project_id + dataset_id`.

La API de producción descrita en `README-FLUJO-PROCESAMIENTO.md` permite subir
ZIP crudos, descargarlos y cambiar el estado del lote a `processing` o `done`.
El estado `done` no adjunta archivos de resultado. La VM de MapTiler mencionada
para otros flujos no forma parte del contrato de estos lotes. Por eso el motor
todavía no puede publicar el ortomosaico en la web.

## Contrato que debe acordarse con el desarrollador de producción

1. **Identificación:** el destino debe aceptar `organizationId`, `batchId` y
   `jobCode` para asociar cada resultado al lote y usuario correctos.
2. **Archivos:** definir cuáles GeoTIFF se entregan por sensor (RGB, MS, DEM y
   recortes por polígono) y si se conserva el TIFF multibanda original. El motor
   debe enviar las bandas en el orden y resolución en que ya las exporta.
3. **Transferencia:** proporcionar una API autenticada que emita URLs temporales
   de carga para archivos grandes, preferentemente directas al almacenamiento
   que ya usa producción. Debe permitir reanudar o repetir una carga sin crear
   resultados duplicados y verificar tamaño e integridad antes de publicarlos.
4. **Publicación:** definir qué servicio prepara los tiles/MapTiler, qué URL o
   identificador devuelve y cómo se vincula al lote para que el usuario lo vea
   en su aplicación web. El lote debe marcarse `done` solo después de completar
   esa publicación; un fallo debe conservar el resultado local para reintento.
5. **Acceso:** la cuenta del motor solo debe poder entregar resultados para los
   lotes que procesa. La web debe comprobar la organización del usuario al
   consultar o abrir el ortomosaico.

## Trabajo local posterior al contrato

Con esos endpoints y permisos definidos, el motor podrá detectar que Metashape
terminó correctamente, enumerar los TIFF finales del proyecto vinculado,
subirlos, guardar el identificador de publicación y actualizar el estado remoto.
Ese envío será independiente del botón de procesar: si falla la red, el proyecto
y sus TIFF permanecerán en esta PC para reintentar la entrega.

Las credenciales del receptor pendientes sirven para **descargar** los ZIP.
No sustituyen el contrato de **entrega** de resultados.
