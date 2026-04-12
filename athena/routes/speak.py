"""Speak (TTS-only) endpoints."""

import base64
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials

from athena.agents.queue import get_agent_job_result
from athena.agents.tts import poll_agent_job_result, submit_tts_job_via_agent
from athena.config import DEFAULT_VOICE
from athena.core.security import verify_token
from athena.models.schemas import SpeakJobResponse, SpeakJobStatusResponse, SpeakRequest

router = APIRouter()


@router.post("/speak")
async def speak_sync(
    request: Request,
    body: SpeakRequest,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Synchronous TTS-only endpoint - returns audio bytes directly."""
    voice = body.speaker_voice or DEFAULT_VOICE
    if not voice:
        raise HTTPException(
            status_code=400,
            detail="No voice specified and DEFAULT_VOICE not configured",
        )

    tts_job_id = await submit_tts_job_via_agent(body.text, voice)
    result = await poll_agent_job_result(tts_job_id, "tts")
    audio_base64 = result.get("audio")
    if not audio_base64:
        raise HTTPException(status_code=500, detail="TTS returned no audio")
    audio_bytes = base64.b64decode(audio_base64)

    return Response(content=audio_bytes, media_type="audio/wav")


@router.post("/speak/job", response_model=SpeakJobResponse, status_code=202)
async def speak_job_submit(
    request: Request,
    body: SpeakRequest,
    background_tasks: BackgroundTasks,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Async TTS-only job submission."""
    voice = body.speaker_voice or DEFAULT_VOICE
    if not voice:
        raise HTTPException(
            status_code=400,
            detail="No voice specified and DEFAULT_VOICE not configured",
        )

    job_id = await submit_tts_job_via_agent(body.text, voice)

    return SpeakJobResponse(job_id=job_id, status="pending")


@router.get("/speak/job/{job_id}", response_model=SpeakJobStatusResponse)
async def speak_job_status(
    job_id: str,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Poll for speak job status."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    result = await get_agent_job_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")

    status = result.get("status", "pending")
    audio = None
    error = result.get("error")
    voice = None

    if status == "completed":
        job_result = result.get("result", {})
        audio = job_result.get("audio") if job_result else None
        payload = result.get("payload", {})
        voice = payload.get("speaker")

    return SpeakJobStatusResponse(
        job_id=job_id,
        status=status,
        audio=audio,
        error=error if status == "failed" else None,
        voice=voice,
    )
