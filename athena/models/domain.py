"""Domain models - internal data structures."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    """Status of a job in the processing pipeline."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PromptJob:
    """A single prompt job with optional TTS."""
    job_id: str
    prompt: str
    speaker: bool
    speaker_voice: Optional[str]
    personality_prompt: Optional[str] = None
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    response_text: Optional[str] = None
    audio_base64: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ConversationJob:
    """A conversation job with message history and optional TTS."""
    job_id: str
    messages: str  # JSON-encoded list of messages
    speaker: bool
    speaker_voice: Optional[str]
    personality_prompt: Optional[str] = None
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    response_text: Optional[str] = None
    audio_base64: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CouncilJob:
    """A council job with message history, member responses, and optional TTS."""
    job_id: str
    messages: str  # JSON-encoded list of messages
    council_members: str  # JSON-encoded list of {name, prompt}
    user_traits: str  # JSON-encoded list of traits
    user_goal: str
    speaker_voice: Optional[str]
    status: str = "pending"
    phase: str = "initial"  # initial, notes, final_notes, synthesis, tts, completed
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    advisor_response: Optional[str] = None
    member_responses: Optional[str] = None  # JSON-encoded list of member responses
    audio_base64: Optional[str] = None
    error: Optional[str] = None
