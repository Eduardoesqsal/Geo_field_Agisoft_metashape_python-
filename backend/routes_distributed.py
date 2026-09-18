import uuid

from fastapi import APIRouter, HTTPException

from backend.queue.redis_queue import enqueue_worker_job, list_worker_states, ping


router = APIRouter(prefix="/api/distributed", tags=["distributed"])


@router.get("/redis/ping")
def redis_ping():
    try:
        return {"ok": bool(ping())}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis no disponible: {exc}") from exc


@router.get("/workers")
def workers():
    try:
        return {"ok": True, "workers": list_worker_states()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"No se pudo leer Redis: {exc}") from exc


@router.post("/workers/{worker_id}/test-job")
def send_test_job(worker_id: str):
    try:
        job = enqueue_worker_job(
            worker_id,
            {
                "job_id": str(uuid.uuid4()),
                "project_name": "Proyecto_Prueba",
                "status": "READY",
                "type": "test",
            },
        )
        return {"ok": True, "message": f"Mensaje enviado a {worker_id}", "job": job}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"No se pudo enviar trabajo: {exc}") from exc
