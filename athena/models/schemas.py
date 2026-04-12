"""API schemas - Pydantic models for request/response validation."""

from typing import List, Optional

from pydantic import BaseModel


# Prompt endpoints
class PromptRequest(BaseModel):
    prompt: str
    speaker: bool = False
    speaker_voice: Optional[str] = None
    personality: Optional[str] = None
    personality_custom: Optional[str] = None


class PromptResponse(BaseModel):
    response: str
    audio: Optional[str] = None


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    response: Optional[str] = None
    audio: Optional[str] = None
    voice: Optional[str] = None
    error: Optional[str] = None


# Conversation endpoints
class ConversationMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ConversationJobRequest(BaseModel):
    messages: List[ConversationMessage]
    speaker: bool = False
    speaker_voice: Optional[str] = None
    personality: Optional[str] = None
    personality_custom: Optional[str] = None


class ConversationStreamJobRequest(BaseModel):
    messages: List[ConversationMessage]
    speaker_voice: Optional[str] = None
    sentence_pause_ms: Optional[int] = None
    personality: Optional[str] = None
    personality_custom: Optional[str] = None


class ConversationJobStatusResponse(BaseModel):
    job_id: str
    status: str
    response: Optional[str] = None
    audio: Optional[str] = None
    voice: Optional[str] = None
    error: Optional[str] = None


# Stream endpoints
class StreamJobRequest(BaseModel):
    prompt: str
    speaker_voice: Optional[str] = None
    sentence_pause_ms: Optional[int] = None
    personality: Optional[str] = None
    personality_custom: Optional[str] = None


class StreamJobResponse(BaseModel):
    job_id: str
    status: str


class SentenceAudio(BaseModel):
    index: int
    text: str
    audio: Optional[str] = None
    status: str


class StreamJobStatusResponse(BaseModel):
    job_id: str
    status: str
    response: Optional[str] = None
    sentences: List[SentenceAudio] = []
    combined_audio: Optional[str] = None
    voice: Optional[str] = None
    error: Optional[str] = None


# Speak endpoints (TTS only)
class SpeakRequest(BaseModel):
    text: str
    speaker_voice: Optional[str] = None


class SpeakJobResponse(BaseModel):
    job_id: str
    status: str


class SpeakJobStatusResponse(BaseModel):
    job_id: str
    status: str
    audio: Optional[str] = None
    error: Optional[str] = None
    voice: Optional[str] = None


# Utility endpoints
class FormatTextRequest(BaseModel):
    text: str


class FormatTextResponse(BaseModel):
    formatted_text: str


class SummarizeRequest(BaseModel):
    text: str
    max_words: int = 6


class SummarizeResponse(BaseModel):
    summary: str


# Agent endpoints
class AgentRegisterRequest(BaseModel):
    agent_id: str
    service_type: str
    speakers: Optional[List[str]] = None


class AgentRegisterResponse(BaseModel):
    status: str
    message: str


class AgentPollRequest(BaseModel):
    agent_id: str
    service_type: str


class AgentJob(BaseModel):
    job_id: str
    service_type: str
    payload: dict
    created_at: float


class AgentPollResponse(BaseModel):
    job: Optional[AgentJob] = None


class AgentCompleteRequest(BaseModel):
    agent_id: str
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None


class AgentCompleteResponse(BaseModel):
    status: str


class AgentHeartbeatRequest(BaseModel):
    agent_id: str
    service_type: str
    speakers: Optional[List[str]] = None


class AgentHeartbeatResponse(BaseModel):
    status: str


class AgentInfo(BaseModel):
    agent_id: str
    service_type: str
    registered_at: float
    last_seen: float
    status: str
    speakers: List[str] = []


class AgentListResponse(BaseModel):
    agents: List[AgentInfo]


# Council endpoints
class CouncilMemberConfig(BaseModel):
    name: str
    prompt: str


class CouncilJobRequest(BaseModel):
    messages: List[ConversationMessage]
    speaker_voice: Optional[str] = None
    council_members: Optional[List[str]] = None
    custom_members: Optional[List[CouncilMemberConfig]] = None
    user_traits: Optional[List[str]] = None
    user_goal: Optional[str] = None


class CouncilStreamJobRequest(BaseModel):
    messages: List[ConversationMessage]
    speaker_voice: Optional[str] = None
    sentence_pause_ms: Optional[int] = None
    council_members: Optional[List[str]] = None
    custom_members: Optional[List[CouncilMemberConfig]] = None
    user_traits: Optional[List[str]] = None
    user_goal: Optional[str] = None


class CouncilMemberNote(BaseModel):
    from_member: str
    note: str


class CouncilMemberResponse(BaseModel):
    name: str
    initial_response: str
    notes_received: List[CouncilMemberNote]
    final_note: str


class CouncilMemberInfo(BaseModel):
    name: str
    prompt: str


class CouncilMembersResponse(BaseModel):
    members: List[CouncilMemberInfo]


class CouncilJobStatusResponse(BaseModel):
    job_id: str
    status: str
    advisor_response: Optional[str] = None
    member_responses: Optional[List[CouncilMemberResponse]] = None
    audio: Optional[str] = None
    voice: Optional[str] = None
    error: Optional[str] = None


class CouncilStreamJobStatusResponse(BaseModel):
    job_id: str
    status: str
    advisor_response: Optional[str] = None
    member_responses: Optional[List[CouncilMemberResponse]] = None
    sentences: List[SentenceAudio] = []
    combined_audio: Optional[str] = None
    voice: Optional[str] = None
    error: Optional[str] = None
