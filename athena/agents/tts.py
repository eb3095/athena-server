"""TTS-specific agent helpers."""

import asyncio

from athena.agents.queue import create_agent_job, get_agent_job_result
from athena.core.redis import get_redis


async def submit_tts_job_via_agent(text: str, speaker: str) -> str:
    """Submit TTS job via agent system."""
    payload = {
        "text": text,
        "speaker": speaker,
    }
    return await create_agent_job("tts", payload)


async def poll_agent_job_result(
    job_id: str, service_type: str, max_wait_seconds: int = 600
) -> dict:
    """Poll for agent job completion. Returns result dict with 'audio' key for TTS jobs."""
    redis = get_redis()
    poll_interval = 2.0
    max_attempts = int(max_wait_seconds / poll_interval)
    attempts = 0

    while attempts < max_attempts:
        await asyncio.sleep(poll_interval)
        attempts += 1

        result = await get_agent_job_result(job_id)
        if not result:
            raise Exception(f"{service_type} job not found")

        status = result.get("status")

        if status == "completed":
            return result.get("result", {})
        elif status == "failed":
            raise Exception(
                f"{service_type} failed: {result.get('error', 'Unknown error')}"
            )
        elif status in ("pending", "assigned"):
            # Periodically check if job is still in queue
            if attempts % 30 == 0:
                in_queue = await redis.lpos(
                    f"jobs:{service_type}:pending", job_id
                )
                if in_queue is None:
                    job_data = await redis.hgetall(f"agent_job:{job_id}")
                    if job_data and job_data.get("status") == "pending":
                        await redis.rpush(f"jobs:{service_type}:pending", job_id)

    raise Exception(f"{service_type} job timed out")
