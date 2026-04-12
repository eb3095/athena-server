"""Conversation endpoints."""

import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from athena.config import (
    DEFAULT_VOICE,
    MAX_CONVERSATION_MESSAGES,
    MAX_PROMPT_LENGTH,
    STREAM_SENTENCE_PAUSE_MS,
    get_personality,
)
from athena.core.security import verify_token
from athena.jobs.processors import process_conversation_job, process_conversation_stream_job
from athena.jobs.storage import (
    get_conversation_job,
    get_conversation_stream_job,
    save_conversation_job,
    save_conversation_stream_job,
)
from athena.models.domain import ConversationJob
from athena.models.schemas import (
    ConversationJobRequest,
    ConversationJobStatusResponse,
    ConversationStreamJobRequest,
    JobSubmitResponse,
    SentenceAudio,
    StreamJobResponse,
    StreamJobStatusResponse,
)

router = APIRouter()


@router.post("/conversation/job", response_model=JobSubmitResponse, status_code=202)
async def submit_conversation_job(
    request: ConversationJobRequest,
    background_tasks: BackgroundTasks,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Submit an async conversation job with message history."""
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages cannot be empty")

    # Enforce rolling window limit
    messages = request.messages[-MAX_CONVERSATION_MESSAGES:]

    # Validate total content length
    total_length = sum(len(m.content) for m in messages)
    if total_length > MAX_PROMPT_LENGTH * 10:
        raise HTTPException(
            status_code=400,
            detail="Total conversation content exceeds maximum length",
        )

    job_id = str(uuid.uuid4())
    messages_json = json.dumps([{"role": m.role, "content": m.content} for m in messages])
    personality_prompt = get_personality(request.personality, request.personality_custom)

    job = ConversationJob(
        job_id=job_id,
        messages=messages_json,
        speaker=request.speaker,
        speaker_voice=request.speaker_voice,
        personality_prompt=personality_prompt,
    )
    await save_conversation_job(job)

    background_tasks.add_task(process_conversation_job, job_id)

    return JobSubmitResponse(job_id=job_id, status="pending")


@router.get("/conversation/job/{job_id}", response_model=ConversationJobStatusResponse)
async def get_conversation_job_status(
    job_id: str,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Poll for conversation job status."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    job = await get_conversation_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return ConversationJobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        response=job.response_text if job.status == "completed" else None,
        audio=job.audio_base64 if job.status == "completed" else None,
        error=job.error if job.status == "failed" else None,
        voice=(job.speaker_voice or DEFAULT_VOICE) if job.status == "completed" and job.audio_base64 else None,
    )


@router.post("/conversation/stream/job", response_model=StreamJobResponse, status_code=202)
async def submit_conversation_stream_job(
    request: ConversationStreamJobRequest,
    background_tasks: BackgroundTasks,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Submit a streaming conversation job that processes sentences in parallel."""
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages cannot be empty")

    # Enforce rolling window limit
    messages = request.messages[-MAX_CONVERSATION_MESSAGES:]

    # Validate total content length
    total_length = sum(len(m.content) for m in messages)
    if total_length > MAX_PROMPT_LENGTH * 10:
        raise HTTPException(
            status_code=400,
            detail="Total conversation content exceeds maximum length",
        )

    voice = request.speaker_voice or DEFAULT_VOICE
    if not voice:
        raise HTTPException(
            status_code=400,
            detail="No voice specified and DEFAULT_VOICE not configured",
        )

    pause_ms = request.sentence_pause_ms if request.sentence_pause_ms is not None else STREAM_SENTENCE_PAUSE_MS
    messages_json = json.dumps([{"role": m.role, "content": m.content} for m in messages])
    personality_prompt = get_personality(request.personality, request.personality_custom)

    initial_sentences = ["Processing..."]

    job_id = str(uuid.uuid4())
    await save_conversation_stream_job(job_id, messages_json, voice, initial_sentences, pause_ms, personality_prompt)

    background_tasks.add_task(process_conversation_stream_job, job_id)

    return StreamJobResponse(job_id=job_id, status="pending")


@router.get("/conversation/stream/job/{job_id}", response_model=StreamJobStatusResponse)
async def get_conversation_stream_job_status(
    job_id: str,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Poll for conversation streaming job status with individual sentence audio."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    job = await get_conversation_stream_job(job_id)
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
