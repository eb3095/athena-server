"""Utility endpoints - format text, summarize, personalities."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from athena.config import (
    FORMAT_TEXT_PREPROMPT,
    MAX_PROMPT_LENGTH,
    PERSONALITIES,
    SUMMARIZE_PREPROMPT,
)
from athena.core.openai import call_openai
from athena.core.security import security, verify_token
from athena.models.schemas import (
    FormatTextRequest,
    FormatTextResponse,
    SummarizeRequest,
    SummarizeResponse,
)

router = APIRouter()


@router.get("/personalities")
async def get_personalities(
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Get available personalities. Returns list of {key, personality} objects."""
    return {"personalities": PERSONALITIES}


@router.post("/format/text", response_model=FormatTextResponse)
async def format_text(
    request: FormatTextRequest,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Format/clean up STT output text using AI."""
    if not request.text.strip():
        return FormatTextResponse(formatted_text="")

    if len(request.text) > MAX_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Text exceeds maximum length of {MAX_PROMPT_LENGTH} characters",
        )

    formatted = await call_openai(FORMAT_TEXT_PREPROMPT, request.text, temperature=0.1)
    return FormatTextResponse(formatted_text=formatted.strip())


@router.post("/summarize", response_model=SummarizeResponse)
async def summarize_text(
    request: SummarizeRequest,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Generate a short summary/title from text."""
    if not request.text.strip():
        return SummarizeResponse(summary="New conversation")

    if len(request.text) > MAX_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Text exceeds maximum length of {MAX_PROMPT_LENGTH} characters",
        )

    prompt = SUMMARIZE_PREPROMPT.format(max_words=request.max_words)
    summary = await call_openai(prompt, request.text, temperature=0.3)

    # Ensure it doesn't exceed max words
    words = summary.strip().split()
    if len(words) > request.max_words:
        summary = " ".join(words[:request.max_words])

    return SummarizeResponse(summary=summary.strip())
