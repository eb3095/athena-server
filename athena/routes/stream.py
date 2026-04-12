"""Stream job endpoints."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from athena.config import (
    DEFAULT_VOICE,
    MAX_PROMPT_LENGTH,
    STREAM_SENTENCE_PAUSE_MS,
    get_personality,
)
from athena.core.security import verify_token
from athena.jobs.processors import process_stream_job
from athena.jobs.storage import get_stream_job, save_stream_job
from athena.models.schemas import (
    SentenceAudio,
    StreamJobRequest,
    StreamJobResponse,
    StreamJobStatusResponse,
)

router = APIRouter()


@router.post("/stream/job", response_model=StreamJobResponse, status_code=202)
async def submit_stream_job(
    body: StreamJobRequest,
    background_tasks: BackgroundTasks,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Submit a streaming prompt job that processes sentences in parallel."""
    if len(body.prompt) > MAX_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Prompt exceeds maximum length of {MAX_PROMPT_LENGTH} characters",
        )

    voice = body.speaker_voice or DEFAULT_VOICE
    if not voice:
        raise HTTPException(
            status_code=400,
            detail="No voice specified and DEFAULT_VOICE not configured",
        )

    pause_ms = body.sentence_pause_ms if body.sentence_pause_ms is not None else STREAM_SENTENCE_PAUSE_MS

    initial_sentences = ["Processing..."]
    personality_prompt = get_personality(body.personality, body.personality_custom)

    job_id = str(uuid.uuid4())
    await save_stream_job(job_id, body.prompt, voice, initial_sentences, pause_ms, personality_prompt)

    background_tasks.add_task(process_stream_job, job_id)

    return StreamJobResponse(job_id=job_id, status="pending")


@router.get("/stream/job/{job_id}", response_model=StreamJobStatusResponse)
async def get_stream_job_status(
    job_id: str,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Poll for streaming job status with individual sentence audio."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    job = await get_stream_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    sentences = [
        SentenceAudio(
            index=s["index"],
            text=s["text"],
            audio=s["audio"],
            status=s["status"],
        )
        for s in job["sentences"]
    ]

    return StreamJobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        response=job["response_text"],
        sentences=sentences,
        combined_audio=job["combined_audio"] if job["status"] == "completed" else None,
        voice=job["voice"],
        error=job["error"] if job["status"] == "failed" else None,
    )
