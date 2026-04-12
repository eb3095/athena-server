"""Voice management endpoints."""

import os
import re

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials

from athena.config import VOICES_DIR
from athena.core.security import verify_agent_key, verify_token, verify_token_or_agent_key
from athena.voices.service import (
    delete_voice_file,
    get_available_voices,
    get_voice_checksum,
    get_voice_names,
    save_voice_file,
)

router = APIRouter()


@router.get("/voices")
async def voices(
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Get available voice names from server storage."""
    return {"voices": get_voice_names()}


@router.get("/voices/list")
async def voices_list(
    request: Request,
    _: None = Depends(verify_token_or_agent_key),
):
    """Get available voices with metadata (name, checksum, size). Accepts Bearer token or Agent Key."""
    return {"voices": get_available_voices()}


@router.get("/voices/{name}/download")
async def voice_download(
    name: str,
    request: Request,
    _: None = Depends(verify_agent_key),
):
    """Download a voice file. Requires agent key authentication."""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid voice name")

    filepath = os.path.join(VOICES_DIR, f"{safe_name}.wav")

    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail=f"Voice '{safe_name}' not found")

    try:
        async with aiofiles.open(filepath, "rb") as f:
            content = await f.read()

        return Response(
            content=content,
            media_type="audio/wav",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}.wav"',
                "X-Voice-Checksum": get_voice_checksum(filepath)
            }
        )
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read voice file: {e}")


@router.post("/voices/{name}/upload")
async def voice_upload(
    name: str,
    request: Request,
    _: None = Depends(verify_agent_key),
):
    """Upload a voice file. Requires agent key authentication."""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid voice name")

    try:
        content = await request.body()
        if not content:
            raise HTTPException(status_code=400, detail="No content provided")

        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large (max 50MB)")

        result = await save_voice_file(safe_name, content)
        return {"status": "success", "voice": result}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save voice file: {e}")


@router.delete("/voices/{name}")
async def voice_delete(
    name: str,
    request: Request,
    _: None = Depends(verify_agent_key),
):
    """Delete a voice file. Requires agent key authentication."""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid voice name")

    filepath = os.path.join(VOICES_DIR, f"{safe_name}.wav")

    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail=f"Voice '{safe_name}' not found")

    if await delete_voice_file(safe_name):
        return {"status": "success", "message": f"Voice '{safe_name}' deleted"}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete voice file")
