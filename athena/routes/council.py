"""Council routes - advisory council endpoints."""

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException

from athena.config import (
    COUNCIL_MEMBERS,
    DEFAULT_VOICE,
    MAX_CONVERSATION_MESSAGES,
    MAX_PROMPT_LENGTH,
    STREAM_SENTENCE_PAUSE_MS,
    get_council_members,
)
from athena.core.security import verify_token
from athena.jobs.council import process_council_job, process_council_stream_job
from athena.jobs.storage import (
    get_council_job,
    get_council_member_data,
    get_council_stream_job,
    save_council_job,
    save_council_stream_job,
)
from athena.models.domain import CouncilJob
from athena.models.schemas import (
    CouncilJobRequest,
    CouncilJobStatusResponse,
    CouncilMemberInfo,
    CouncilMemberNote,
    CouncilMemberResponse,
    CouncilMembersResponse,
    CouncilStreamJobRequest,
    CouncilStreamJobStatusResponse,
    JobSubmitResponse,
    SentenceAudio,
)

router = APIRouter()


@router.get("/council/members", response_model=CouncilMembersResponse)
async def get_council_members_list(_: str = Depends(verify_token)):
    """Get list of available council members."""
    members = [
        CouncilMemberInfo(name=m["name"], prompt=m["prompt"])
        for m in COUNCIL_MEMBERS
    ]
    return CouncilMembersResponse(members=members)


@router.post("/council/job", response_model=JobSubmitResponse)
async def submit_council_job(
    request: CouncilJobRequest,
    _: str = Depends(verify_token),
):
    """Submit a council job for processing."""
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages cannot be empty")

    messages = request.messages[-MAX_CONVERSATION_MESSAGES:]
    total_length = sum(len(m.content) for m in messages)
    if total_length > MAX_PROMPT_LENGTH * 10:
        raise HTTPException(status_code=400, detail="Conversation too long")

    custom_members = None
    if request.custom_members:
        custom_members = [{"name": m.name, "prompt": m.prompt} for m in request.custom_members]
    
    council_members = get_council_members(request.council_members, custom_members)
    
    if not council_members:
        raise HTTPException(status_code=400, detail="No council members selected")

    job_id = str(uuid.uuid4())
    job = CouncilJob(
        job_id=job_id,
        messages=json.dumps([{"role": m.role, "content": m.content} for m in messages]),
        council_members=json.dumps(council_members),
        user_traits=json.dumps(request.user_traits or []),
        user_goal=request.user_goal or "",
        speaker_voice=request.speaker_voice,
    )
    await save_council_job(job)

    task = asyncio.create_task(process_council_job(job_id))
    task.add_done_callback(lambda t: t.exception() if t.done() and not t.cancelled() else None)

    return JobSubmitResponse(job_id=job_id, status="pending")


@router.get("/council/job/{job_id}", response_model=CouncilJobStatusResponse)
async def get_council_job_status(
    job_id: str,
    _: str = Depends(verify_token),
):
    """Get council job status."""
    job = await get_council_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    member_responses = None
    if job.member_responses:
        try:
            raw_responses = json.loads(job.member_responses)
            member_responses = [
                CouncilMemberResponse(
                    name=r["name"],
                    initial_response=r.get("initial_response", ""),
                    notes_received=[
                        CouncilMemberNote(
                            from_member=n["from_member"],
                            note=n["note"],
                        )
                        for n in r.get("notes_received", [])
                    ],
                    final_note=r.get("final_note", ""),
                )
                for r in raw_responses
            ]
        except (json.JSONDecodeError, KeyError):
            pass

    return CouncilJobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        advisor_response=job.advisor_response,
        member_responses=member_responses,
        audio=job.audio_base64,
        voice=job.speaker_voice,
        error=job.error,
    )


@router.post("/council/stream/job", response_model=JobSubmitResponse)
async def submit_council_stream_job(
    request: CouncilStreamJobRequest,
    _: str = Depends(verify_token),
):
    """Submit a council streaming job for processing."""
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages cannot be empty")

    voice = request.speaker_voice or DEFAULT_VOICE
    if not voice:
        raise HTTPException(
            status_code=400,
            detail="No voice specified and DEFAULT_VOICE not configured"
        )

    messages = request.messages[-MAX_CONVERSATION_MESSAGES:]
    total_length = sum(len(m.content) for m in messages)
    if total_length > MAX_PROMPT_LENGTH * 10:
        raise HTTPException(status_code=400, detail="Conversation too long")

    custom_members = None
    if request.custom_members:
        custom_members = [{"name": m.name, "prompt": m.prompt} for m in request.custom_members]
    
    council_members = get_council_members(request.council_members, custom_members)
    
    if not council_members:
        raise HTTPException(status_code=400, detail="No council members selected")

    job_id = str(uuid.uuid4())
    pause_ms = request.sentence_pause_ms or STREAM_SENTENCE_PAUSE_MS

    await save_council_stream_job(
        job_id=job_id,
        messages=json.dumps([{"role": m.role, "content": m.content} for m in messages]),
        council_members=json.dumps(council_members),
        user_traits=json.dumps(request.user_traits or []),
        user_goal=request.user_goal or "",
        voice=voice,
        sentences=["placeholder"],
        pause_ms=pause_ms,
    )

    task = asyncio.create_task(process_council_stream_job(job_id))
    task.add_done_callback(lambda t: t.exception() if t.done() and not t.cancelled() else None)

    return JobSubmitResponse(job_id=job_id, status="pending")


@router.get("/council/stream/job/{job_id}", response_model=CouncilStreamJobStatusResponse)
async def get_council_stream_job_status(
    job_id: str,
    _: str = Depends(verify_token),
):
    """Get council streaming job status."""
    job = await get_council_stream_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    member_responses = None
    if job.get("member_responses"):
        try:
            raw_responses = json.loads(job["member_responses"])
            member_responses = [
                CouncilMemberResponse(
                    name=r["name"],
                    initial_response=r.get("initial_response", ""),
                    notes_received=[
                        CouncilMemberNote(
                            from_member=n["from_member"],
                            note=n["note"],
                        )
                        for n in r.get("notes_received", [])
                    ],
                    final_note=r.get("final_note", ""),
                )
                for r in raw_responses
            ]
        except (json.JSONDecodeError, KeyError):
            pass

    sentences = [
        SentenceAudio(
            index=s["index"],
            text=s["text"],
            audio=s["audio"],
            status=s["status"],
        )
        for s in job.get("sentences", [])
    ]

    return CouncilStreamJobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        advisor_response=job.get("advisor_response"),
        member_responses=member_responses,
        sentences=sentences,
        combined_audio=job.get("combined_audio"),
        voice=job.get("voice"),
        error=job.get("error"),
    )
