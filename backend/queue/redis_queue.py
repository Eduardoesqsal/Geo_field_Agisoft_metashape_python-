import json
import os
from datetime import datetime, timezone

import redis


DEFAULT_REDIS_HOST = "localhost"
DEFAULT_REDIS_PORT = 6379
JOB_QUEUE_PREFIX = "metashape:jobs"
WORKER_STATE_PREFIX = "metashape:workers"


def redis_settings():
    return {
        "host": os.environ.get("REDIS_HOST", DEFAULT_REDIS_HOST),
        "port": int(os.environ.get("REDIS_PORT", DEFAULT_REDIS_PORT)),
        "decode_responses": True,
    }


def get_redis_client():
    return redis.Redis(**redis_settings())


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def job_queue_key(worker_id):
    worker = (worker_id or "").strip()
    if not worker:
        raise ValueError("worker_id requerido")
    return f"{JOB_QUEUE_PREFIX}:{worker}"


def worker_state_key(worker_id):
    worker = (worker_id or "").strip()
    if not worker:
        raise ValueError("worker_id requerido")
    return f"{WORKER_STATE_PREFIX}:{worker}"


def ping():
    return get_redis_client().ping()


def enqueue_worker_job(worker_id, payload):
    client = get_redis_client()
    job = dict(payload or {})
    job.setdefault("worker_id", worker_id)
    job.setdefault("queued_at", utc_now_iso())
    client.rpush(job_queue_key(worker_id), json.dumps(job, ensure_ascii=False))
    return job


def dequeue_worker_job(worker_id, timeout=5):
    client = get_redis_client()
    item = client.blpop(job_queue_key(worker_id), timeout=timeout)
    if item is None:
        return None
    _key, raw = item
    return json.loads(raw)


def update_worker_state(worker_id, status, current_job=None, extra=None, ttl_seconds=45):
    client = get_redis_client()
    payload = {
        "worker_id": worker_id,
        "status": status,
        "current_job": current_job,
        "last_seen": utc_now_iso(),
    }
    if extra:
        payload.update(extra)
    client.setex(worker_state_key(worker_id), ttl_seconds, json.dumps(payload, ensure_ascii=False))
    return payload


def list_worker_states():
    client = get_redis_client()
    states = []
    for key in sorted(client.scan_iter(f"{WORKER_STATE_PREFIX}:*")):
        raw = client.get(key)
        if not raw:
            continue
        try:
            states.append(json.loads(raw))
        except json.JSONDecodeError:
            states.append({"worker_id": key.rsplit(":", 1)[-1], "status": "ERROR", "raw": raw})
    return states
