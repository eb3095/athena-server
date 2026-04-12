"""Agent management endpoints."""

import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from athena.agents.queue import complete_agent_job, poll_agent_job
from athena.config import AGENT_HEARTBEAT_INTERVAL, AGENT_MISSED_HEARTBEATS, AGENT_RETENTION_SECONDS
from athena.core.redis import get_redis
from athena.core.security import verify_agent_key, verify_token
from athena.models.schemas import (
    AgentCompleteRequest,
    AgentCompleteResponse,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentInfo,
    AgentJob,
    AgentListResponse,
    AgentPollRequest,
    AgentPollResponse,
    AgentRegisterRequest,
    AgentRegisterResponse,
)

router = APIRouter()


@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(
    request: Request,
    body: AgentRegisterRequest,
):
    """Register a new agent."""
    verify_agent_key(request)
    redis = get_redis()

    agent_key = f"agent:{body.service_type}:{body.agent_id}"
    mapping = {
        "agent_id": body.agent_id,
        "service_type": body.service_type,
        "registered_at": str(time.time()),
        "last_seen": str(time.time()),
    }
    if body.speakers is not None:
        mapping["speakers"] = json.dumps(body.speakers)

    await redis.hset(agent_key, mapping=mapping)
    await redis.expire(agent_key, AGENT_RETENTION_SECONDS)

    return AgentRegisterResponse(status="ok", message="Agent registered")


@router.post("/jobs/poll", response_model=AgentPollResponse)
async def poll_agent_jobs(
    request: Request,
    body: AgentPollRequest,
):
    """Poll for available jobs."""
    verify_agent_key(request)
    redis = get_redis()

    agent_key = f"agent:{body.service_type}:{body.agent_id}"
    await redis.hset(agent_key, "last_seen", str(time.time()))
    await redis.expire(agent_key, AGENT_RETENTION_SECONDS)

    job = await poll_agent_job(body.agent_id, body.service_type)

    if job:
        return AgentPollResponse(job=AgentJob(**job))
    return AgentPollResponse(job=None)


@router.post("/jobs/{job_id}/complete", response_model=AgentCompleteResponse)
async def complete_agent_job_endpoint(
    request: Request,
    job_id: str,
    body: AgentCompleteRequest,
):
    """Mark a job as completed."""
    verify_agent_key(request)

    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    success = await complete_agent_job(
        job_id=job_id,
        agent_id=body.agent_id,
        status=body.status,
        result=body.result,
        error=body.error,
    )

    if not success:
        raise HTTPException(
            status_code=404, detail="Job not found or not assigned to this agent"
        )

    return AgentCompleteResponse(status="ok")


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
async def agent_heartbeat(
    request: Request,
    body: AgentHeartbeatRequest,
):
    """Agent heartbeat to maintain registration."""
    verify_agent_key(request)
    redis = get_redis()

    agent_key = f"agent:{body.service_type}:{body.agent_id}"
    exists = await redis.exists(agent_key)

    if not exists:
        mapping = {
            "agent_id": body.agent_id,
            "service_type": body.service_type,
            "registered_at": str(time.time()),
            "last_seen": str(time.time()),
        }
        if body.speakers is not None:
            mapping["speakers"] = json.dumps(body.speakers)
        await redis.hset(agent_key, mapping=mapping)
    else:
        updates = {"last_seen": str(time.time())}
        if body.speakers is not None:
            updates["speakers"] = json.dumps(body.speakers)
        await redis.hset(agent_key, mapping=updates)

    await redis.expire(agent_key, AGENT_RETENTION_SECONDS)

    return AgentHeartbeatResponse(status="ok")


@router.get("", response_model=AgentListResponse)
async def list_agents(
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """List all registered agents with their status and capabilities."""
    redis = get_redis()
    agents = []
    now = time.time()
    dead_threshold = AGENT_HEARTBEAT_INTERVAL * AGENT_MISSED_HEARTBEATS

    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="agent:*:*", count=100)

        for key in keys:
            data = await redis.hgetall(key)
            if not data or "agent_id" not in data:
                continue

            last_seen = float(data.get("last_seen", 0))
            seconds_since_seen = now - last_seen
            status = "dead" if seconds_since_seen > dead_threshold else "active"

            speakers = []
            if data.get("speakers"):
                try:
                    speakers = json.loads(data["speakers"])
                except json.JSONDecodeError:
                    pass

            agents.append(
                AgentInfo(
                    agent_id=data["agent_id"],
                    service_type=data.get("service_type", "unknown"),
                    registered_at=float(data.get("registered_at", 0)),
                    last_seen=last_seen,
                    status=status,
                    speakers=speakers,
                )
            )

        if cursor == 0:
            break

    return AgentListResponse(agents=agents)
