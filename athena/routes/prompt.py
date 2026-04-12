"""Prompt endpoints."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from athena.agents.tts import poll_agent_job_result, submit_tts_job_via_agent
from athena.config import (
    DEFAULT_VOICE,
    FORMATTING_PREPROMPT,
    MAX_PROMPT_LENGTH,
    TTS_CONVERSION_PREPROMPT,
    get_personality,
)
from athena.core.openai import call_openai
from athena.core.security import verify_token
from athena.jobs.processors import process_prompt_job
from athena.jobs.storage import get_job, save_job
from athena.models.domain import PromptJob
from athena.models.schemas import (
    JobStatusResponse,
    JobSubmitResponse,
    PromptRequest,
    PromptResponse,
)

router = APIRouter()


@router.post("/prompt", response_model=PromptResponse)
async def prompt(
    request: PromptRequest,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Synchronous prompt endpoint."""
    if len(request.prompt) > MAX_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Prompt exceeds maximum length of {MAX_PROMPT_LENGTH} characters",
        )

    personality = get_personality(request.personality, request.personality_custom)

    if not request.speaker:
        system_prompt = f"{FORMATTING_PREPROMPT}\n\n{personality}"
        response_text = await call_openai(system_prompt, request.prompt)
        return PromptResponse(response=response_text)

    voice = request.speaker_voice or DEFAULT_VOICE
    if not voice:
        raise HTTPException(
            status_code=400,
            detail="No voice specified and DEFAULT_VOICE not configured",
        )

    display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{personality}"
    display_response = await call_openai(display_system_prompt, request.prompt)

    tts_text = await call_openai(
        TTS_CONVERSION_PREPROMPT, display_response, temperature=0.1
    )

    tts_job_id = await submit_tts_job_via_agent(tts_text, voice)
    result = await poll_agent_job_result(tts_job_id, "tts")
    audio_base64 = result.get("audio")

    return PromptResponse(response=display_response, audio=audio_base64)


@router.post("/prompt/job", response_model=JobSubmitResponse, status_code=202)
async def submit_prompt_job(
    request: PromptRequest,
    background_tasks: BackgroundTasks,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Async prompt job submission."""
    if len(request.prompt) > MAX_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Prompt exceeds maximum length of {MAX_PROMPT_LENGTH} characters",
        )

    job_id = str(uuid.uuid4())
    personality_prompt = get_personality(request.personality, request.personality_custom)
    job = PromptJob(
        job_id=job_id,
        prompt=request.prompt,
        speaker=request.speaker,
        speaker_voice=request.speaker_voice,
        personality_prompt=personality_prompt,
    )
    await save_job(job)

    background_tasks.add_task(process_prompt_job, job_id)

    return JobSubmitResponse(job_id=job_id, status="pending")


@router.get("/prompt/job/{job_id}", response_model=JobStatusResponse)
async def get_prompt_job_status(
    job_id: str,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Poll for prompt job status."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        response=job.response_text if job.status == "completed" else None,
        audio=job.audio_base64 if job.status == "completed" else None,
        error=job.error if job.status == "failed" else None,
        voice=(job.speaker_voice or DEFAULT_VOICE) if job.status == "completed" and job.audio_base64 else None,
    )
