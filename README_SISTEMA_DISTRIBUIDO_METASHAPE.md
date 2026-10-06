# Sistema Distribuido de Procesamiento Fotogramétrico con Agisoft Metashape

## 1. Objetivo del proyecto

Este proyecto busca convertir una aplicación fotogramétrica existente en un sistema de procesamiento distribuido.

Existe una **PC Central** que contiene la aplicación Web y coordina los trabajos.

Además existen inicialmente **5 computadoras Worker**:

- PC1
- PC2
- PC3
- PC4
- PC5

Cada Worker tiene instalado Agisoft Metashape y debe utilizar sus propios recursos:

- CPU
- GPU
- RAM
- SSD

El objetivo **NO** es dividir un mismo proyecto entre varias computadoras.

El objetivo es ejecutar **proyectos fotogramétricos diferentes simultáneamente**.

```text
PC1 → Proyecto A
PC2 → Proyecto B
PC3 → Proyecto C
PC4 → Proyecto D
PC5 → Proyecto E
```

Las cinco computadoras pueden procesar al mismo tiempo.

---

## 2. Principio fundamental

```text
                         USUARIO
                            │
                            ▼
                     WEB APP CENTRAL
                     React + Leaflet
                            │
                            ▼
                         FastAPI
                            │
               ┌────────────┴────────────┐
               │                         │
               ▼                         ▼
          Base de datos                Redis
                                     Cola/estado
                                         │
                  ┌──────────┬───────────┼───────────┬──────────┐
                  ▼          ▼           ▼           ▼          ▼
                 PC1        PC2         PC3         PC4        PC5
               Worker     Worker      Worker      Worker     Worker
                  │          │           │           │          │
                  ▼          ▼           ▼           ▼          ▼
              Metashape  Metashape   Metashape   Metashape  Metashape
```

La PC Central funciona como:
- interfaz del usuario;
- coordinador;
- servidor FastAPI;
- servidor Redis;
- almacenamiento principal;
- administrador de proyectos;
- receptor de resultados.

Los Workers funcionan como máquinas de procesamiento.

---

## 3. Flujo esperado

Supongamos que a las 08:00 llega `Proyecto_A`.

El usuario carga las fotografías desde la Web App. La PC Central registra:

```text
Proyecto_A
Estado: pendiente
Fotografías: 1,245
```

El servidor consulta qué Workers están disponibles:

```text
PC1 → disponible
PC2 → disponible
PC3 → disponible
PC4 → disponible
PC5 → disponible
```

El usuario puede seleccionar manualmente:

```text
Proyecto_A → PC3
```

Entonces:

1. La PC Central prepara el proyecto.
2. Las fotografías se transfieren a PC3.
3. PC3 recibe una orden mediante Redis.
4. PC3 ejecuta Agisoft Metashape.
5. PC3 utiliza su CPU/GPU/RAM/SSD.
6. PC3 genera el ortomosaico.
7. El resultado regresa a la PC Central.
8. La PC Central registra el proyecto como completado.
9. PC3 vuelve a estado `disponible`.

---

## 4. Procesamiento paralelo

Si a las 12:00 llega `Proyecto_B`, puede asignarse a PC2 mientras PC3 continúa con Proyecto A:

```text
PC2 → Proyecto B → procesando
PC3 → Proyecto A → procesando
```

Si después llega Proyecto C:

```text
PC1 → Proyecto C
PC2 → Proyecto B
PC3 → Proyecto A
```

Los proyectos son independientes y se procesan simultáneamente.

---

## 5. Cola cuando no hay Workers disponibles

Si las cinco PCs están ocupadas, los proyectos nuevos deben permanecer:

```text
PENDING / EN COLA
```

Cuando una computadora termine, vuelve a `AVAILABLE` y puede recibir otro proyecto.

Debe soportarse:
- asignación manual del Worker;
- posteriormente, asignación automática.

La primera implementación debe priorizar la asignación manual.

---

## 6. Redis

Redis funcionará en la PC Central.

Redis **NO** debe almacenar fotografías, ortomosaicos, TIFF, PSX ni datasets completos.

Redis se utilizará para:
- órdenes;
- colas;
- estados;
- heartbeat;
- progreso;
- identificación de Workers y Jobs.

Ejemplo:

```text
Worker PC1 → disponible
Worker PC2 → ocupado
Worker PC3 → disponible
Worker PC4 → ocupado
Worker PC5 → disponible
```

---

## 7. Redis mediante Docker

En la PC Central:

```bash
docker run -d --name redis-metashape -p 6379:6379 redis:7
```

Comprobar:

```bash
docker ps
```

Durante desarrollo, la PC Central usa:

```text
localhost:6379
```

Los Workers utilizarán la IP privada de la PC Central, configurada mediante variables de entorno:

```env
REDIS_HOST=192.168.1.25
REDIS_PORT=6379
```

No hardcodear esta IP en el código.

**Seguridad:** Redis debe permanecer dentro de la red local. No exponer el puerto 6379 públicamente a Internet.

---

## 8. Comunicación con Workers

Cada computadora Worker ejecuta un agente:

```text
backend/workers/agent.py
```

El agente permanece funcionando y esperando órdenes:

```text
PC3 iniciada
Worker PC3 conectado.
Estado: disponible.
Esperando trabajo...
```

Cuando recibe `Proyecto_A`:

```text
PC3
disponible
↓
procesando Proyecto_A
```

Cuando termina:

```text
PC3
procesando
↓
disponible
```

---

## 9. Identificación del Worker

No crear cinco versiones diferentes de `agent.py`.

Debe existir un único agente. Puede detectar la máquina con:

```python
import socket

worker_id = socket.gethostname()
```

Preferiblemente permitir configuración explícita:

```env
WORKER_ID=PC3
```

---

## 10. Cola individual por Worker

Como el usuario quiere seleccionar específicamente qué PC procesará cada proyecto, utilizar colas específicas:

```text
metashape:jobs:PC1
metashape:jobs:PC2
metashape:jobs:PC3
metashape:jobs:PC4
metashape:jobs:PC5
```

Ejemplo:

```python
redis.rpush("metashape:jobs:PC3", job_json)
```

PC3 escucha únicamente:

```text
metashape:jobs:PC3
```

Así un trabajo dirigido a PC3 no puede ser tomado por PC1, PC2, PC4 o PC5.

---

## 11. Modelo real de Job

No enviar únicamente `"Proyecto_A"`.

La implementación debe evolucionar hacia un objeto Job:

```json
{
  "job_id": "uuid",
  "project_id": "uuid",
  "project_name": "Proyecto_A",
  "worker_id": "PC3",
  "camera_model": "mavic_3m",
  "input_path": "C:\\MetashapeJobs\\Proyecto_A\\dataset",
  "output_path": "C:\\MetashapeJobs\\Proyecto_A\\proyecto",
  "status": "pending"
}
```

Preferir IDs únicos.

---

## 12. Estados de Job

Como mínimo:

```text
PENDING
TRANSFERRING
READY
PROCESSING
COMPLETED
FAILED
```

Opcional:

```text
CANCELLED
```

Flujo normal:

```text
PENDING
   ↓
TRANSFERRING
   ↓
READY
   ↓
PROCESSING
   ↓
COMPLETED
```

---

## 13. Estados del Worker

Como mínimo:

```text
AVAILABLE
BUSY
OFFLINE
```

Opcional:

```text
ERROR
```

Ejemplo:

```json
{
  "worker_id": "PC3",
  "status": "BUSY",
  "project_id": "uuid",
  "progress": 45,
  "last_seen": "timestamp"
}
```

---

## 14. Heartbeat

Cada agente debe enviar periódicamente un heartbeat, aproximadamente cada 5–15 segundos.

Guardar:
- worker_id;
- status;
- current_job;
- last_seen.

Si desaparece el heartbeat durante un tiempo razonable:

```text
PC3 → OFFLINE
```

No asignar nuevos trabajos a ese Worker.

---

## 15. Código actual existente

Ya existe un pipeline funcional. **No reescribirlo innecesariamente.**

```text
backend/
├── application.py
├── config.py
├── main.py
├── routes.py
├── runtime.py
├── workflow.py
└── services/
    ├── ingestion.py
    ├── overlay.py
    └── process.py

frontend/
└── src/
    ├── App.jsx
    ├── main.jsx
    └── styles.css

dataset/
datasets_crudos/
proyecto/
logs/
Dockerfile
requirements.txt
```

---

## 16. `workflow.py` existente

Existe:

```python
class ProcesamientoMetashape:
```

con:

```python
cargar_fotos()
alinear_camaras()
construir_profundidad()
construir_modelo()
construir_ortomosaico()
exportar_resultado()
```

No modificar los algoritmos de Metashape salvo que sea necesario para soportar rutas por proyecto.

---

## 17. `main.py` existente

Actualmente ejecuta:

```python
app = ProcesamientoMetashape(camera_model=camera_model)

app.cargar_fotos()
app.alinear_camaras()
app.construir_profundidad()
app.construir_modelo()
app.construir_ortomosaico()
app.exportar_resultado()
```

También crea logs, captura excepciones y limpia temporales.

El Worker debe reutilizar esta lógica:

```text
agent.py
   ↓
main.py / servicio de ejecución
   ↓
workflow.py
   ↓
Metashape
```

No duplicar las seis etapas dentro del Worker.

---

## 18. Problema actual: rutas globales

Actualmente `workflow.py` importa:

```python
from backend.config import (
    RUTA_IMAGENES,
    RUTA_ORTOMOSAICO_MS,
    RUTA_ORTOMOSAICO_RGB,
    RUTA_PROYECTO
)
```

Para procesamiento distribuido, cada Job debe poder especificar sus propias rutas:

```text
C:\MetashapeJobs\Proyecto_A\dataset
C:\MetashapeJobs\Proyecto_A\proyecto

C:\MetashapeJobs\Proyecto_B\dataset
C:\MetashapeJobs\Proyecto_B\proyecto
```

Preferir inyección de rutas/configuración en `ProcesamientoMetashape(...)` en lugar de depender exclusivamente de constantes globales.

Mantener compatibilidad durante la refactorización.

---

## 19. Estructura local del Worker

Cada Worker puede utilizar:

```text
C:\MetashapeJobs\
```

Ejemplo:

```text
C:\MetashapeJobs\
├── Proyecto_A\
│   ├── dataset\
│   ├── proyecto\
│   └── logs\
└── Proyecto_C\
    ├── dataset\
    ├── proyecto\
    └── logs\
```

---

## 20. Transferencia de fotografías

Redis no transporta fotografías.

Primera implementación recomendada: **SMB / carpetas compartidas de Windows**.

Ejemplo:

```text
PC Central
    │
    │ copia fotografías
    ▼
\\PC3\MetashapeJobs\Proyecto_A\dataset
```

Solo cuando termine correctamente la transferencia:

```text
Job → READY
```

Después se envía la orden mediante Redis.

No iniciar Metashape mientras las fotografías todavía se están copiando.

---

## 21. Procesamiento desde SSD local

Preferentemente:

```text
PC CENTRAL
    │
    │ copia
    ▼
SSD PC3
    │
    ▼
Metashape
    │
    ├── CPU PC3
    ├── GPU PC3
    └── RAM PC3
```

Para datasets grandes, preferir Ethernet Gigabit o superior frente a Wi-Fi.

---

## 22. Resultado

Al terminar:

```text
PC3

C:\MetashapeJobs\Proyecto_A\
└── proyecto\
    └── ortomosaico_rgb.tif
```

El resultado regresa a la PC Central:

```text
PC3
ortomosaico_rgb.tif
       │
       ▼
PC CENTRAL
Proyecto_A/
└── resultados/
    └── ortomosaico_rgb.tif
```

Solo marcar `COMPLETED` cuando la central confirme que recibió correctamente el resultado.

---

## 23. Limpieza

Secuencia:

```text
Metashape termina
↓
resultado generado
↓
resultado transferido
↓
central confirma
↓
COMPLETED
↓
opcionalmente limpiar archivos temporales
```

La limpieza debe ser configurable.

---

## 24. Progreso

`ProcesamientoMetashape` soporta `progress_callback`.

Actualmente reporta:

```text
[1/6] Cargando fotos
[2/6] Alineando camaras
[3/6] Construyendo profundidad
[4/6] Construyendo modelo
[5/6] Construyendo ortomosaico
[6/6] Exportando resultado
```

El Worker puede publicar estas etapas en Redis:

```json
{
  "worker": "PC3",
  "project": "Proyecto_A",
  "stage": 3,
  "total_stages": 6,
  "message": "Construyendo profundidad"
}
```

No inventar porcentajes precisos si Metashape no los proporciona.

---

## 25. Web App

El frontend React no debe conectarse directamente a Redis.

```text
React
   ↓ HTTP
FastAPI
   ↓
Redis
```

Endpoints posibles:

```text
GET  /api/workers
GET  /api/projects
GET  /api/projects/{id}
POST /api/projects/{id}/assign
GET  /api/jobs/{id}
```

---

## 26. Endpoint de asignación

Conceptualmente:

```http
POST /api/projects/{project_id}/assign
```

Body:

```json
{
  "worker_id": "PC3"
}
```

El backend debe:

1. comprobar que existe el proyecto;
2. comprobar que existe PC3;
3. comprobar heartbeat;
4. comprobar que PC3 está AVAILABLE;
5. marcar Job como TRANSFERRING;
6. transferir archivos;
7. verificar transferencia;
8. marcar READY;
9. colocar Job en `metashape:jobs:PC3`;
10. cambiar PC3 a BUSY.

Evitar condiciones de carrera.

---

## 27. Dashboard esperado

```text
WORKERS

PC1  🟢 Disponible
PC2  🟡 Procesando Proyecto B
PC3  🟢 Disponible
PC4  🟡 Procesando Proyecto D
PC5  🔴 Offline
```

Proyectos:

```text
Proyecto A   Completado
Proyecto B   Procesando   PC2
Proyecto C   En cola
Proyecto D   Procesando   PC4
```

---

## 28. Visualización de ortomosaicos

Ya existe:

```text
backend/services/overlay.py
```

La visualización utiliza React + Leaflet.

Los overlays pertenecen a un proyecto.

No mostrar automáticamente todos los ortomosaicos simultáneamente.

```text
Proyecto seleccionado: Proyecto_A

Mapa base
+
Overlay Proyecto_A
```

Si se selecciona Proyecto B, ocultar/eliminar el overlay anterior y cargar el de B.

---

## 29. Capas futuras

Por proyecto:

```text
☑ Ortomosaico RGB
☐ Ortomosaico MS
☐ DSM
☐ DTM
☐ Detecciones
☐ Índices
```

Siempre asociadas al proyecto seleccionado.

---

## 30. Persistencia

Redis no debe ser la fuente permanente de verdad.

Redis:

```text
colas
heartbeats
estado operativo
mensajes
```

Base de datos persistente:

```text
usuarios
proyectos
jobs
workers registrados
asignaciones
historial
resultados
fechas
errores
```

Si ya existe PostgreSQL/Supabase, mantener esa responsabilidad separada de Redis.

---

## 31. Arquitectura de código propuesta

Agregar sin destruir la estructura existente:

```text
backend/
│
├── domain/
│   ├── __init__.py
│   ├── job.py
│   └── worker.py
│
├── queue/
│   ├── __init__.py
│   └── redis_queue.py
│
├── workers/
│   ├── __init__.py
│   ├── agent.py
│   └── heartbeat.py
│
├── services/
│   ├── ingestion.py
│   ├── overlay.py
│   ├── process.py
│   ├── orchestrator.py
│   └── transfer.py
│
├── workflow.py
├── main.py
├── routes.py
├── config.py
└── application.py
```

---

## 32. Responsabilidades por archivo

### `domain/job.py`

Representar `Job` y `JobStatus`. No colocar Redis aquí.

### `domain/worker.py`

Representar `Worker` y `WorkerStatus`. No colocar FastAPI/Redis directamente en las entidades.

### `queue/redis_queue.py`

Interacción con Redis:
- enqueue;
- dequeue;
- worker state;
- heartbeat;
- job progress.

### `workers/agent.py`

Proceso ejecutado en cada Worker:
- registrarse;
- heartbeat;
- esperar trabajos;
- ejecutar procesamiento;
- reportar estado;
- reportar errores;
- transferir resultado.

### `workers/heartbeat.py`

Mantener presencia del Worker.

### `services/orchestrator.py`

Coordinar:

```text
Proyecto
↓
Worker
↓
Transferencia
↓
Redis
↓
Procesamiento
```

### `services/transfer.py`

Encapsular:

```text
fotografías → Worker
resultados → Central
```

No mezclar transferencia de archivos con lógica de Metashape.

---

## 33. Variables de entorno

PC Central:

```env
REDIS_HOST=localhost
REDIS_PORT=6379
JOBS_ROOT=C:\Proyectos
```

Worker:

```env
WORKER_ID=PC3
REDIS_HOST=192.168.1.25
REDIS_PORT=6379
WORKER_JOBS_ROOT=C:\MetashapeJobs
```

Adaptar rutas según cada sistema.

---

## 34. Fases de implementación

### Fase 1 — Redis

Levantar Redis:

```bash
docker run -d --name redis-metashape -p 6379:6379 redis:7
docker ps
```

### Fase 2 — Conexión Python

Verificar:

```python
r.ping()
```

Resultado:

```text
True
```

### Fase 3 — Worker mínimo

Objetivo:

```text
PC3 conectada
Esperando trabajos...
```

### Fase 4 — Heartbeat

Objetivo:

```text
PC1 AVAILABLE
PC2 AVAILABLE
PC3 AVAILABLE
PC4 AVAILABLE
PC5 AVAILABLE
```

### Fase 5 — Colas específicas

Probar:

```text
Central → Proyecto prueba → PC3
```

Solo PC3 debe recibirlo.

### Fase 6 — Modelo Job

Sustituir mensajes simples por un objeto Job estructurado.

### Fase 7 — Rutas por proyecto

Refactorizar las rutas de Metashape manteniendo compatibilidad.

### Fase 8 — Integración con Metashape

Conectar Job con `main.py`/`workflow.py`, primero con un dataset pequeño.

### Fase 9 — Transferencia de entrada

```text
Central → dataset → Worker
```

### Fase 10 — Procesamiento local

Worker procesa desde su SSD usando su CPU/GPU/RAM.

### Fase 11 — Transferencia de resultado

```text
Worker → ortomosaico → Central
```

### Fase 12 — Endpoints FastAPI

Exponer Workers, proyectos, Jobs y asignaciones.

### Fase 13 — Dashboard React

Mostrar estado de Workers y proyectos.

### Fase 14 — Overlay por proyecto

Mostrar únicamente las capas correspondientes al proyecto seleccionado.

---

## 35. Reglas IMPORTANTES para el agente de programación

1. NO reescribir el pipeline funcional de Metashape.
2. NO eliminar código existente sin justificarlo.
3. Hacer cambios incrementales.
4. Mantener separación por capas.
5. NO meter fotografías o TIFF en Redis.
6. NO conectar React directamente a Redis.
7. NO hardcodear IPs.
8. NO hardcodear rutas específicas de una PC dentro de lógica de dominio.
9. Utilizar variables de entorno/configuración.
10. Mantener logs por proyecto y Worker.
11. No marcar un proyecto `COMPLETED` hasta verificar que el resultado llegó correctamente.
12. No borrar resultados locales prematuramente.
13. No asignar trabajos a Workers sin heartbeat válido.
14. Diseñar pensando en que mañana puedan existir 10, 20 o más Workers.
15. Evitar que dos Workers reclamen accidentalmente el mismo Job.
16. Cada Worker procesa como máximo un proyecto a la vez inicialmente.
17. Un proyecto pertenece a un único Worker durante una ejecución.
18. Si un Worker falla, registrar `FAILED`; no asumir que terminó.
19. Mantener compatibilidad con Windows y Agisoft Metashape.
20. Antes de una refactorización grande, explicar qué archivos cambiarán y por qué.

---

## 36. Resultado final esperado

El usuario abre únicamente la Web App de la PC Central:

```text
PC1 🟢
PC2 🟢
PC3 🟢
PC4 🟢
PC5 🟢
```

Carga `Proyecto A`, selecciona `PC3` y presiona `PROCESAR`.

```text
Fotografías
↓
PC3
↓
Metashape
↓
CPU/GPU/RAM de PC3
↓
Ortomosaico
↓
PC Central
↓
overlay.py
↓
React + Leaflet
↓
Mapa
```

Simultáneamente puede existir:

```text
PC1 → Proyecto C
PC2 → Proyecto B
PC3 → Proyecto A
PC4 → Proyecto D
PC5 → Proyecto E
```

El procesamiento paralelo de **proyectos independientes** es el objetivo principal.

---

## 37. Primera tarea para el agente

**NO comenzar modificando `workflow.py`.**

Primero:

1. inspeccionar la estructura actual;
2. revisar `requirements.txt`;
3. revisar `config.py`;
4. revisar `routes.py`;
5. revisar `services/process.py`;
6. verificar cómo se ejecuta actualmente `backend/main.py`;
7. agregar Redis de manera aislada;
8. implementar una prueba mínima de conexión;
9. explicar los cambios realizados;
10. detenerse y validar que el sistema actual siga funcionando.

La primera meta técnica es únicamente:

```text
PC Central
    ↓
Redis
    ↓
Worker de prueba
    ↓
mensaje recibido correctamente
```

**SIN ejecutar todavía un procesamiento real de Metashape.**

Una vez validado, continuar incrementalmente con:

```text
Heartbeat
↓
Asignación específica por Worker
↓
Transferencia de archivos
↓
Integración con Metashape
↓
Retorno del ortomosaico
↓
Visualización en la Web App
```
