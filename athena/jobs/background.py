"""Background tasks - job recovery, timeout, cleanup."""

import asyncio
import time

from athena.config import (
    AGENT_JOB_TIMEOUT_MINUTES,
    AGENT_JOB_TIMEOUT_SECONDS,
    COMPLETED_JOB_RETENTION_SECONDS,
)
from athena.core.redis import get_redis


async def recover_expired_agent_jobs():
    """Background task to recover jobs from dead agents."""
    redis = get_redis()
    while True:
        await asyncio.sleep(60)

        try:
            cursor = 0
            while True:
                cursor, keys = await redis.scan(
                    cursor, match="agent_job:*", count=100
                )

                for key in keys:
                    job_data = await redis.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status != "assigned":
                        continue

                    job_id = job_data.get("job_id")
                    service_type = job_data.get("service_type")

                    assigned = await redis.exists(
                        f"jobs:{service_type}:assigned:{job_id}"
                    )
                    if not assigned:
                        await redis.hset(key, "status", "pending")
                        await redis.hdel(key, "assigned_to", "assigned_at")
                        await redis.rpush(f"jobs:{service_type}:pending", job_id)

                if cursor == 0:
                    break
        except Exception as e:
            print(f"Error in job recovery task: {e}")


async def timeout_stale_jobs():
    """Background task to mark jobs as failed if they don't complete in time."""
    redis = get_redis()
    while True:
        await asyncio.sleep(60)

        try:
            now = time.time()

            # Timeout agent jobs (TTS jobs etc)
            cursor = 0
            while True:
                cursor, keys = await redis.scan(
                    cursor, match="agent_job:*", count=100
                )

                for key in keys:
                    job_data = await redis.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status not in ("pending", "assigned"):
                        continue

                    created_at = float(job_data.get("created_at", 0))
                    if now - created_at > AGENT_JOB_TIMEOUT_SECONDS:
                        job_id = job_data.get("job_id")
                        if status == "pending":
                            error_msg = "No agents available to process job"
                        else:
                            error_msg = f"Job timed out after {AGENT_JOB_TIMEOUT_MINUTES} minutes"
                        print(
                            f"Timing out agent job {job_id} ({status}, age: {int(now - created_at)}s)"
                        )
                        await redis.hset(
                            key,
                            mapping={
                                "status": "failed",
                                "error": error_msg,
                            },
                        )

                if cursor == 0:
                    break

            # Timeout main jobs (prompt jobs, speak jobs)
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor, match="job:*", count=100)

                for key in keys:
                    job_data = await redis.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status not in ("pending", "processing"):
                        continue

                    created_at = float(job_data.get("created_at", 0))
                    if now - created_at > AGENT_JOB_TIMEOUT_SECONDS:
                        job_id = job_data.get("job_id")
                        if status == "pending":
                            error_msg = "No agents available to process job"
                        else:
                            error_msg = f"Job timed out after {AGENT_JOB_TIMEOUT_MINUTES} minutes"
                        print(
                            f"Timing out job {job_id} ({status}, age: {int(now - created_at)}s)"
                        )
                        await redis.hset(
                            key,
                            mapping={
                                "status": "failed",
                                "error": error_msg,
                            },
                        )

                if cursor == 0:
                    break
        except Exception as e:
            print(f"Error in job timeout task: {e}")


async def cleanup_completed_jobs():
    """Background task to delete old completed/failed jobs."""
    redis = get_redis()
    while True:
        await asyncio.sleep(300)

        try:
            now = time.time()
            deleted_count = 0

            # Cleanup agent jobs
            cursor = 0
            while True:
                cursor, keys = await redis.scan(
                    cursor, match="agent_job:*", count=100
                )

                for key in keys:
                    job_data = await redis.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status not in ("completed", "failed"):
                        continue

                    created_at = float(job_data.get("created_at", 0))
                    if now - created_at > COMPLETED_JOB_RETENTION_SECONDS:
                        await redis.delete(key)
                        deleted_count += 1

                if cursor == 0:
                    break

            # Cleanup main jobs
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor, match="job:*", count=100)

                for key in keys:
                    job_data = await redis.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status not in ("completed", "failed"):
                        continue

                    created_at = float(job_data.get("created_at", 0))
                    if now - created_at > COMPLETED_JOB_RETENTION_SECONDS:
                        await redis.delete(key)
                        deleted_count += 1

                if cursor == 0:
                    break

            if deleted_count > 0:
                print(f"Cleaned up {deleted_count} old completed/failed jobs")
        except Exception as e:
            print(f"Error in job cleanup task: {e}")


async def recover_stale_jobs():
    """Recover jobs that were processing when server restarted."""
    # Import here to avoid circular imports
    from athena.jobs.processors import (
        process_conversation_job,
        process_conversation_stream_job,
        process_prompt_job,
        process_stream_job,
    )

    redis = get_redis()
    job_patterns = [
        ("conversation_stream_job:*", process_conversation_stream_job),
        ("conversation_job:*", process_conversation_job),
        ("stream_job:*", process_stream_job),
        ("prompt_job:*", process_prompt_job),
    ]

    for pattern, processor_func in job_patterns:
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match=pattern, count=100)

            for key in keys:
                # Skip sentence sub-keys
                if ":sentence:" in key:
                    continue

                job_data = await redis.hgetall(key)
                if not job_data:
                    continue

                status = job_data.get("status")
                if status == "processing":
                    job_id = job_data.get("job_id")
                    if job_id:
                        print(f"Recovering stale job: {key} (status={status})", flush=True)
                        # Reset to pending so it can be re-processed
                        await redis.hset(key, "status", "pending")
                        # Re-spawn the background task
                        asyncio.create_task(processor_func(job_id))

            if cursor == 0:
                break


async def start_background_tasks():
    """Start all background tasks. Returns list of task handles."""
    tasks = [
        asyncio.create_task(recover_expired_agent_jobs()),
        asyncio.create_task(timeout_stale_jobs()),
        asyncio.create_task(cleanup_completed_jobs()),
    ]
    return tasks
