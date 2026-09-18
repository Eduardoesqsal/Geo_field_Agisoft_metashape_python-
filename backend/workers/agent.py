import os
import socket
import time

from backend.queue.redis_queue import dequeue_worker_job, update_worker_state


def worker_id_from_env():
    return os.environ.get("WORKER_ID") or socket.gethostname()


def main():
    worker_id = worker_id_from_env()
    print(f"Worker {worker_id} conectado.", flush=True)
    print("Esperando trabajos...", flush=True)

    while True:
        update_worker_state(worker_id, "AVAILABLE")
        job = dequeue_worker_job(worker_id, timeout=10)
        if job is None:
            continue

        update_worker_state(worker_id, "BUSY", current_job=job.get("job_id") or job.get("project_name"))
        print(f"Trabajo recibido por {worker_id}: {job}", flush=True)

        # Primera prueba distribuida: solo confirma recepcion. Metashape todavia no se ejecuta.
        time.sleep(1)
        update_worker_state(worker_id, "AVAILABLE")
        print(f"Trabajo confirmado por {worker_id}", flush=True)


if __name__ == "__main__":
    main()
