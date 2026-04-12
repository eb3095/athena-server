"""Agent job queue operations."""

import json
import time
import uuid
from typing import Optional

from athena.config import AGENT_JOB_TTL_SECONDS, JOB_EXPIRY_SECONDS
from athena.core.redis import get_redis


async def create_agent_job(service_type: str, payload: dict) -> str:
    """Create a new agent job and add to pending queue."""
    redis = get_redis()
    job_id = str(uuid.uuid4())
    job_data = {
        "job_id": job_id,
        "service_type": service_type,
        "payload": json.dumps(payload),
        "created_at": str(time.time()),
        "status": "pending",
    }

    await redis.hset(f"agent_job:{job_id}", mapping=job_data)
    await redis.expire(f"agent_job:{job_id}", JOB_EXPIRY_SECONDS * 2)
    await redis.rpush(f"jobs:{service_type}:pending", job_id)

    return job_id


async def poll_agent_job(agent_id: str, service_type: str) -> Optional[dict]:
    """Atomically claim a job from the pending queue."""
    redis = get_redis()
    job_id = await redis.lpop(f"jobs:{service_type}:pending")
    if not job_id:
        return None

    job_data = await redis.hgetall(f"agent_job:{job_id}")
    if not job_data or "job_id" not in job_data:
        return None

    await redis.set(
        f"jobs:{service_type}:assigned:{job_id}", agent_id, ex=AGENT_JOB_TTL_SECONDS
    )

    await redis.hset(f"agent_job:{job_id}", "status", "assigned")
    await redis.hset(f"agent_job:{job_id}", "assigned_to", agent_id)
    await redis.hset(f"agent_job:{job_id}", "assigned_at", str(time.time()))

    return {
        "job_id": job_data["job_id"],
        "service_type": job_data["service_type"],
        "payload": json.loads(job_data.get("payload", "{}")),
        "created_at": float(job_data["created_at"]),
    }


async def complete_agent_job(
    job_id: str,
    agent_id: str,
    status: str,
    result: Optional[dict],
    error: Optional[str],
) -> bool:
    """Mark a job as completed or failed."""
    redis = get_redis()
    job_key = f"agent_job:{job_id}"
    job_data = await redis.hgetall(job_key)

    if not job_data:
        return False

    service_type = job_data.get("service_type", "")
    assigned_to = job_data.get("assigned_to", "")
    if assigned_to != agent_id:
        return False

    updates = {
        "status": status,
        "completed_at": str(time.time()),
    }
    if result:
        updates["result"] = json.dumps(result)
    if error:
        updates["error"] = error

    await redis.hset(job_key, mapping=updates)
    await redis.expire(job_key, JOB_EXPIRY_SECONDS)
    await redis.delete(f"jobs:{service_type}:assigned:{job_id}")

    return True


async def get_agent_job_result(job_id: str) -> Optional[dict]:
    """Get the result of an agent job."""
    redis = get_redis()
    job_data = await redis.hgetall(f"agent_job:{job_id}")
    if not job_data:
        return None

    return {
        "job_id": job_data.get("job_id"),
        "status": job_data.get("status"),
        "result": json.loads(job_data.get("result", "null")),
        "error": job_data.get("error"),
        "payload": json.loads(job_data.get("payload", "{}")),
    }
