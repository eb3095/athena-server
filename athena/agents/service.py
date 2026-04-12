"""Agent service - registration and management."""

import time
from typing import List

from athena.config import (
    AGENT_HEARTBEAT_INTERVAL,
    AGENT_MISSED_HEARTBEATS,
    AGENT_RETENTION_SECONDS,
)
from athena.core.redis import get_redis


async def register_agent(
    agent_id: str, service_type: str, speakers: List[str] = None
) -> dict:
    """Register an agent with the server."""
    redis = get_redis()
    key = f"agent:{service_type}:{agent_id}"
    now = time.time()

    agent_data = {
        "agent_id": agent_id,
        "service_type": service_type,
        "registered_at": str(now),
        "last_seen": str(now),
        "status": "active",
        "speakers": ",".join(speakers) if speakers else "",
    }

    await redis.hset(key, mapping=agent_data)
    await redis.expire(key, AGENT_RETENTION_SECONDS)

    return {"status": "registered", "message": f"Agent {agent_id} registered"}


async def agent_heartbeat(
    agent_id: str, service_type: str, speakers: List[str] = None
) -> dict:
    """Update agent heartbeat."""
    redis = get_redis()
    key = f"agent:{service_type}:{agent_id}"
    now = time.time()

    exists = await redis.exists(key)
    if not exists:
        return await register_agent(agent_id, service_type, speakers)

    updates = {"last_seen": str(now)}
    if speakers is not None:
        updates["speakers"] = ",".join(speakers)

    await redis.hset(key, mapping=updates)
    await redis.expire(key, AGENT_RETENTION_SECONDS)

    return {"status": "ok"}


async def list_agents() -> List[dict]:
    """List all registered agents with their status."""
    redis = get_redis()
    now = time.time()
    agents = []

    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="agent:*", count=100)

        for key in keys:
            agent_data = await redis.hgetall(key)
            if not agent_data:
                continue

            last_seen = float(agent_data.get("last_seen", 0))
            time_since_seen = now - last_seen
            max_time = AGENT_HEARTBEAT_INTERVAL * AGENT_MISSED_HEARTBEATS

            status = "active" if time_since_seen < max_time else "inactive"

            speakers_str = agent_data.get("speakers", "")
            speakers = speakers_str.split(",") if speakers_str else []

            agents.append({
                "agent_id": agent_data.get("agent_id", ""),
                "service_type": agent_data.get("service_type", ""),
                "registered_at": float(agent_data.get("registered_at", 0)),
                "last_seen": last_seen,
                "status": status,
                "speakers": speakers,
            })

        if cursor == 0:
            break

    return agents
