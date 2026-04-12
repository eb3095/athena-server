"""API routes."""

from fastapi import APIRouter

from athena.routes import (
    prompt,
    conversation,
    stream,
    speak,
    council,
    agents,
    voices,
    utilities,
    health,
)

router = APIRouter()

router.include_router(prompt.router, prefix="/api", tags=["prompt"])
router.include_router(conversation.router, prefix="/api", tags=["conversation"])
router.include_router(stream.router, prefix="/api", tags=["stream"])
router.include_router(speak.router, prefix="/api", tags=["speak"])
router.include_router(council.router, prefix="/api", tags=["council"])
router.include_router(agents.router, prefix="/api/agents", tags=["agents"])
router.include_router(voices.router, prefix="/api", tags=["voices"])
router.include_router(utilities.router, prefix="/api", tags=["utilities"])
router.include_router(health.router, tags=["health"])
