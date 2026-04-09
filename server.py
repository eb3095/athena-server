from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from openai import AsyncOpenAI
import uvicorn
import base64
import io
import json
import os
import re
import secrets
import struct
import time
import uuid
import wave
import asyncio
import redis.asyncio as redis
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, List
from collections import defaultdict

openai_client: Optional[AsyncOpenAI] = None
redis_client: Optional[redis.Redis] = None
background_tasks: list = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global openai_client, redis_client

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required")

    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
    except Exception as e:
        raise RuntimeError(f"Redis connection failed: {e}")

    background_tasks.append(asyncio.create_task(recover_expired_agent_jobs()))
    background_tasks.append(asyncio.create_task(timeout_stale_jobs()))
    background_tasks.append(asyncio.create_task(cleanup_completed_jobs()))

    yield

    if redis_client:
        await redis_client.aclose()


app = FastAPI(lifespan=lifespan)
security = HTTPBearer()

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
OPENAI_TEMPERATURE = float(os.environ.get("OPENAI_TEMPERATURE", "0.7"))
OPENAI_MAX_TOKENS = int(os.environ.get("OPENAI_MAX_TOKENS", "4096"))

DEFAULT_VOICE = os.environ.get("DEFAULT_VOICE", "")

DEFAULT_FORMATTING_PREPROMPT = "Format your response using markdown when appropriate. Use headers, bullet points, code blocks, and emphasis to make the response clear and readable."
DEFAULT_PERSONALITY_PREPROMPT = "You are a helpful AI assistant."
DEFAULT_TTS_CONVERSION_PREPROMPT = "Convert this text to spoken form for a user who is speaking, not typing. Keep it as close to the original wording as possible. Only make minimal changes: remove markdown symbols, convert lists to sentences, spell out abbreviations. Do not rephrase, summarize, or add anything. Never say words like 'bullet', 'asterisk', or 'code block'. Output only the converted text."

FORMATTING_PREPROMPT = os.environ.get(
    "FORMATTING_PREPROMPT", DEFAULT_FORMATTING_PREPROMPT
)
PERSONALITY_PREPROMPT = os.environ.get(
    "PERSONALITY_PREPROMPT", DEFAULT_PERSONALITY_PREPROMPT
)
TTS_CONVERSION_PREPROMPT = os.environ.get(
    "TTS_CONVERSION_PREPROMPT", DEFAULT_TTS_CONVERSION_PREPROMPT
)

RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", "300"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
AUTH_FAIL_BAN_THRESHOLD = int(os.environ.get("AUTH_FAIL_BAN_THRESHOLD", "3"))
AUTH_FAIL_BAN_DURATION_SECONDS = int(
    os.environ.get("AUTH_FAIL_BAN_DURATION_SECONDS", "604800")
)
MAX_PROMPT_LENGTH = int(os.environ.get("MAX_PROMPT_LENGTH", "10000"))
OPENAI_TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT", "120.0"))

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
JOB_EXPIRY_SECONDS = int(os.environ.get("JOB_EXPIRY_SECONDS", "3600"))

AGENT_KEY = os.environ.get("AGENT_KEY", "")
AGENT_JOB_TTL_SECONDS = int(os.environ.get("AGENT_JOB_TTL_SECONDS", "300"))
AGENT_HEARTBEAT_INTERVAL = 60
AGENT_MISSED_HEARTBEATS = int(os.environ.get("AGENT_MISSED_HEARTBEATS", "3"))
AGENT_RETENTION_DAYS = int(os.environ.get("AGENT_RETENTION_DAYS", "30"))
AGENT_RETENTION_SECONDS = AGENT_RETENTION_DAYS * 24 * 60 * 60
AGENT_JOB_TIMEOUT_MINUTES = int(os.environ.get("AGENT_JOB_TIMEOUT_MINUTES", "30"))
AGENT_JOB_TIMEOUT_SECONDS = AGENT_JOB_TIMEOUT_MINUTES * 60
COMPLETED_JOB_RETENTION_HOURS = int(
    os.environ.get("COMPLETED_JOB_RETENTION_HOURS", "6")
)
COMPLETED_JOB_RETENTION_SECONDS = COMPLETED_JOB_RETENTION_HOURS * 60 * 60

STREAM_SENTENCE_PAUSE_MS = int(os.environ.get("STREAM_SENTENCE_PAUSE_MS", "500"))


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PromptJob:
    job_id: str
    prompt: str
    speaker: bool
    speaker_voice: Optional[str]
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    response_text: Optional[str] = None
    audio_base64: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ConversationJob:
    job_id: str
    messages: str  # JSON-encoded list of messages
    speaker: bool
    speaker_voice: Optional[str]
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    response_text: Optional[str] = None
    audio_base64: Optional[str] = None
    error: Optional[str] = None


rate_limit_store: dict[str, list[float]] = defaultdict(list)
auth_fail_store: dict[str, list[float]] = defaultdict(list)
banned_ips: dict[str, float] = {}


class PromptRequest(BaseModel):
    prompt: str
    speaker: bool = False
    speaker_voice: Optional[str] = None


class PromptResponse(BaseModel):
    response: str
    audio: Optional[str] = None


class AgentRegisterRequest(BaseModel):
    agent_id: str
    service_type: str
    speakers: Optional[list[str]] = None


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
    speakers: Optional[list[str]] = None


class AgentHeartbeatResponse(BaseModel):
    status: str


class AgentInfo(BaseModel):
    agent_id: str
    service_type: str
    registered_at: float
    last_seen: float
    status: str
    speakers: list[str] = []


class AgentListResponse(BaseModel):
    agents: list[AgentInfo]


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


class StreamJobRequest(BaseModel):
    prompt: str
    speaker_voice: Optional[str] = None
    sentence_pause_ms: Optional[int] = None


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


class ConversationMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ConversationJobRequest(BaseModel):
    messages: List[ConversationMessage]
    speaker: bool = False
    speaker_voice: Optional[str] = None


class ConversationStreamJobRequest(BaseModel):
    messages: List[ConversationMessage]
    speaker_voice: Optional[str] = None
    sentence_pause_ms: Optional[int] = None


class ConversationJobStatusResponse(BaseModel):
    job_id: str
    status: str
    response: Optional[str] = None
    audio: Optional[str] = None
    voice: Optional[str] = None
    error: Optional[str] = None


class FormatTextRequest(BaseModel):
    text: str


class FormatTextResponse(BaseModel):
    formatted_text: str


class SummarizeRequest(BaseModel):
    text: str
    max_words: int = 6


class SummarizeResponse(BaseModel):
    summary: str


MAX_CONVERSATION_MESSAGES = int(os.environ.get("MAX_CONVERSATION_MESSAGES", "20"))


def split_into_sentences(text: str) -> List[str]:
    """Split text into sentences for streaming TTS."""
    sentence_endings = re.compile(r'(?<=[.!?])\s+')
    sentences = sentence_endings.split(text.strip())
    return [s.strip() for s in sentences if s.strip()]


def generate_silence_wav(duration_ms: int, sample_rate: int = 22050) -> bytes:
    """Generate silence as WAV bytes."""
    num_samples = int(sample_rate * duration_ms / 1000)
    silence = b'\x00\x00' * num_samples
    
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(silence)
    
    return buffer.getvalue()


def combine_wav_audio(audio_segments: List[bytes], pause_ms: int = 500) -> bytes:
    """Combine multiple WAV audio segments with pauses between them."""
    if not audio_segments:
        return b''
    
    if len(audio_segments) == 1:
        return audio_segments[0]
    
    first_wav = io.BytesIO(audio_segments[0])
    with wave.open(first_wav, 'rb') as w:
        sample_rate = w.getframerate()
        sample_width = w.getsampwidth()
        n_channels = w.getnchannels()
    
    all_frames = []
    silence_frames = b'\x00' * int(sample_rate * pause_ms / 1000) * sample_width * n_channels
    
    for i, audio_data in enumerate(audio_segments):
        if i > 0 and pause_ms > 0:
            all_frames.append(silence_frames)
        
        wav_buffer = io.BytesIO(audio_data)
        with wave.open(wav_buffer, 'rb') as wav_file:
            all_frames.append(wav_file.readframes(wav_file.getnframes()))
    
    output_buffer = io.BytesIO()
    with wave.open(output_buffer, 'wb') as output_wav:
        output_wav.setnchannels(n_channels)
        output_wav.setsampwidth(sample_width)
        output_wav.setframerate(sample_rate)
        output_wav.writeframes(b''.join(all_frames))
    
    return output_buffer.getvalue()


def get_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_ip_banned(ip: str) -> bool:
    if ip in banned_ips:
        ban_expiry = banned_ips[ip]
        if time.time() < ban_expiry:
            return True
        del banned_ips[ip]
    return False


def record_auth_failure(ip: str):
    now = time.time()
    window_start = now - AUTH_FAIL_BAN_DURATION_SECONDS
    auth_fail_store[ip] = [t for t in auth_fail_store[ip] if t > window_start]
    auth_fail_store[ip].append(now)
    if len(auth_fail_store[ip]) >= AUTH_FAIL_BAN_THRESHOLD:
        banned_ips[ip] = now + AUTH_FAIL_BAN_DURATION_SECONDS
        del auth_fail_store[ip]


def check_rate_limit(ip: str):
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    rate_limit_store[ip] = [t for t in rate_limit_store[ip] if t > window_start]
    if len(rate_limit_store[ip]) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW_SECONDS} seconds.",
        )
    rate_limit_store[ip].append(now)


def verify_token(
    request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)
):
    ip = get_client_ip(request)

    if is_ip_banned(ip):
        raise HTTPException(status_code=401, detail="Unauthorized")

    check_rate_limit(ip)

    if not AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="AUTH_TOKEN not configured")

    if not secrets.compare_digest(credentials.credentials, AUTH_TOKEN):
        record_auth_failure(ip)
        raise HTTPException(status_code=401, detail="Unauthorized")

    return credentials


def verify_agent_key(request: Request):
    """Verify agent key from X-Agent-Key header."""
    ip = get_client_ip(request)

    if is_ip_banned(ip):
        raise HTTPException(status_code=401, detail="Unauthorized")

    check_rate_limit(ip)

    if not AGENT_KEY:
        raise HTTPException(status_code=500, detail="AGENT_KEY not configured")

    agent_key = request.headers.get("X-Agent-Key", "")
    if not secrets.compare_digest(agent_key, AGENT_KEY):
        record_auth_failure(ip)
        raise HTTPException(status_code=401, detail="Unauthorized")


async def call_openai(
    system_prompt: str, user_prompt: str, temperature: Optional[float] = None
) -> str:
    response = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=temperature if temperature is not None else OPENAI_TEMPERATURE,
        max_tokens=OPENAI_MAX_TOKENS,
        timeout=OPENAI_TIMEOUT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content or ""


async def save_job(job: PromptJob):
    key = f"job:{job.job_id}"
    data = {}
    for k, v in asdict(job).items():
        if v is None:
            data[k] = ""
        elif isinstance(v, bool):
            data[k] = "1" if v else "0"
        else:
            data[k] = str(v)
    await redis_client.hset(key, mapping=data)
    ttl = (
        JOB_EXPIRY_SECONDS
        if job.status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis_client.expire(key, ttl)


async def get_job(job_id: str) -> Optional[PromptJob]:
    key = f"job:{job_id}"
    data = await redis_client.hgetall(key)
    if not data or "job_id" not in data:
        return None
    return PromptJob(
        job_id=data["job_id"],
        prompt=data.get("prompt", ""),
        speaker=data.get("speaker") == "1",
        speaker_voice=data["speaker_voice"] if data.get("speaker_voice") else None,
        status=data.get("status", "failed"),
        created_at=float(data["created_at"]) if data.get("created_at") else 0.0,
        completed_at=float(data["completed_at"]) if data.get("completed_at") else None,
        response_text=data.get("response_text") if data.get("response_text") else None,
        audio_base64=data.get("audio_base64") if data.get("audio_base64") else None,
        error=data.get("error") if data.get("error") else None,
    )


async def update_job_status(job_id: str, status: str, **fields):
    key = f"job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis_client.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis_client.expire(key, ttl)


async def save_stream_job(
    job_id: str,
    prompt: str,
    voice: str,
    sentences: List[str],
    pause_ms: int,
):
    """Save a streaming job with its sentences."""
    key = f"stream_job:{job_id}"
    data = {
        "job_id": job_id,
        "prompt": prompt,
        "voice": voice,
        "status": "pending",
        "pause_ms": str(pause_ms),
        "created_at": str(time.time()),
        "sentence_count": str(len(sentences)),
        "response_text": "",
        "combined_audio": "",
        "error": "",
    }
    await redis_client.hset(key, mapping=data)
    await redis_client.expire(key, JOB_EXPIRY_SECONDS * 2)
    
    for i, sentence in enumerate(sentences):
        sentence_key = f"stream_job:{job_id}:sentence:{i}"
        sentence_data = {
            "index": str(i),
            "text": sentence,
            "audio": "",
            "status": "pending",
            "tts_job_id": "",
        }
        await redis_client.hset(sentence_key, mapping=sentence_data)
        await redis_client.expire(sentence_key, JOB_EXPIRY_SECONDS * 2)


async def get_stream_job(job_id: str) -> Optional[dict]:
    """Get a streaming job with all its sentences."""
    key = f"stream_job:{job_id}"
    data = await redis_client.hgetall(key)
    if not data or "job_id" not in data:
        return None
    
    sentence_count = int(data.get("sentence_count", 0))
    sentences = []
    
    for i in range(sentence_count):
        sentence_key = f"stream_job:{job_id}:sentence:{i}"
        sentence_data = await redis_client.hgetall(sentence_key)
        if sentence_data:
            sentences.append({
                "index": int(sentence_data.get("index", i)),
                "text": sentence_data.get("text", ""),
                "audio": sentence_data.get("audio") or None,
                "status": sentence_data.get("status", "pending"),
            })
    
    return {
        "job_id": data["job_id"],
        "prompt": data.get("prompt", ""),
        "voice": data.get("voice", ""),
        "status": data.get("status", "pending"),
        "pause_ms": int(data.get("pause_ms", STREAM_SENTENCE_PAUSE_MS)),
        "response_text": data.get("response_text") or None,
        "combined_audio": data.get("combined_audio") or None,
        "error": data.get("error") or None,
        "sentences": sentences,
    }


async def update_stream_job_status(job_id: str, status: str, **fields):
    """Update streaming job status."""
    key = f"stream_job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis_client.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis_client.expire(key, ttl)


async def update_stream_sentence(job_id: str, index: int, **fields):
    """Update a sentence's status/audio in a streaming job."""
    key = f"stream_job:{job_id}:sentence:{index}"
    updates = {}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis_client.hset(key, mapping=updates)
    await redis_client.expire(key, JOB_EXPIRY_SECONDS * 2)


async def save_conversation_job(job: ConversationJob):
    """Save a conversation job to Redis."""
    key = f"conversation_job:{job.job_id}"
    data = {}
    for k, v in asdict(job).items():
        if v is None:
            data[k] = ""
        elif isinstance(v, bool):
            data[k] = "1" if v else "0"
        else:
            data[k] = str(v)
    await redis_client.hset(key, mapping=data)
    ttl = (
        JOB_EXPIRY_SECONDS
        if job.status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis_client.expire(key, ttl)


async def get_conversation_job(job_id: str) -> Optional[ConversationJob]:
    """Get a conversation job from Redis."""
    key = f"conversation_job:{job_id}"
    data = await redis_client.hgetall(key)
    if not data or "job_id" not in data:
        return None
    return ConversationJob(
        job_id=data["job_id"],
        messages=data.get("messages", "[]"),
        speaker=data.get("speaker") == "1",
        speaker_voice=data["speaker_voice"] if data.get("speaker_voice") else None,
        status=data.get("status", "failed"),
        created_at=float(data["created_at"]) if data.get("created_at") else 0.0,
        completed_at=float(data["completed_at"]) if data.get("completed_at") else None,
        response_text=data.get("response_text") if data.get("response_text") else None,
        audio_base64=data.get("audio_base64") if data.get("audio_base64") else None,
        error=data.get("error") if data.get("error") else None,
    )


async def update_conversation_job_status(job_id: str, status: str, **fields):
    """Update conversation job status."""
    key = f"conversation_job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis_client.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis_client.expire(key, ttl)


async def save_conversation_stream_job(
    job_id: str,
    messages: str,
    voice: str,
    sentences: List[str],
    pause_ms: int,
):
    """Save a conversation streaming job with its sentences."""
    key = f"conversation_stream_job:{job_id}"
    data = {
        "job_id": job_id,
        "messages": messages,
        "voice": voice,
        "status": "pending",
        "pause_ms": str(pause_ms),
        "created_at": str(time.time()),
        "sentence_count": str(len(sentences)),
        "response_text": "",
        "combined_audio": "",
        "error": "",
    }
    await redis_client.hset(key, mapping=data)
    await redis_client.expire(key, JOB_EXPIRY_SECONDS * 2)
    
    for i, sentence in enumerate(sentences):
        sentence_key = f"conversation_stream_job:{job_id}:sentence:{i}"
        sentence_data = {
            "index": str(i),
            "text": sentence,
            "audio": "",
            "status": "pending",
            "tts_job_id": "",
        }
        await redis_client.hset(sentence_key, mapping=sentence_data)
        await redis_client.expire(sentence_key, JOB_EXPIRY_SECONDS * 2)


async def get_conversation_stream_job(job_id: str) -> Optional[dict]:
    """Get a conversation streaming job with all its sentences."""
    key = f"conversation_stream_job:{job_id}"
    data = await redis_client.hgetall(key)
    if not data or "job_id" not in data:
        return None
    
    sentence_count = int(data.get("sentence_count", 0))
    sentences = []
    
    for i in range(sentence_count):
        sentence_key = f"conversation_stream_job:{job_id}:sentence:{i}"
        sentence_data = await redis_client.hgetall(sentence_key)
        if sentence_data:
            sentences.append({
                "index": int(sentence_data.get("index", i)),
                "text": sentence_data.get("text", ""),
                "audio": sentence_data.get("audio") or None,
                "status": sentence_data.get("status", "pending"),
            })
    
    return {
        "job_id": data["job_id"],
        "messages": data.get("messages", "[]"),
        "voice": data.get("voice", ""),
        "status": data.get("status", "pending"),
        "pause_ms": int(data.get("pause_ms", STREAM_SENTENCE_PAUSE_MS)),
        "response_text": data.get("response_text") or None,
        "combined_audio": data.get("combined_audio") or None,
        "error": data.get("error") or None,
        "sentences": sentences,
    }


async def update_conversation_stream_job_status(job_id: str, status: str, **fields):
    """Update conversation streaming job status."""
    key = f"conversation_stream_job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis_client.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis_client.expire(key, ttl)


async def update_conversation_stream_sentence(job_id: str, index: int, **fields):
    """Update a sentence's status/audio in a conversation streaming job."""
    key = f"conversation_stream_job:{job_id}:sentence:{index}"
    updates = {}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis_client.hset(key, mapping=updates)
    await redis_client.expire(key, JOB_EXPIRY_SECONDS * 2)


async def call_openai_conversation(
    system_prompt: str, messages: List[dict], temperature: Optional[float] = None
) -> str:
    """Call OpenAI with conversation history."""
    openai_messages = [{"role": "system", "content": system_prompt}]
    openai_messages.extend(messages)
    
    response = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=temperature if temperature is not None else OPENAI_TEMPERATURE,
        max_tokens=OPENAI_MAX_TOKENS,
        timeout=OPENAI_TIMEOUT,
        messages=openai_messages,
    )
    return response.choices[0].message.content or ""


async def create_agent_job(service_type: str, payload: dict) -> str:
    """Create a new agent job and add to pending queue."""
    job_id = str(uuid.uuid4())
    job_data = {
        "job_id": job_id,
        "service_type": service_type,
        "payload": json.dumps(payload),
        "created_at": str(time.time()),
        "status": "pending",
    }

    await redis_client.hset(f"agent_job:{job_id}", mapping=job_data)
    await redis_client.expire(f"agent_job:{job_id}", JOB_EXPIRY_SECONDS * 2)
    await redis_client.rpush(f"jobs:{service_type}:pending", job_id)

    return job_id


async def poll_agent_job(agent_id: str, service_type: str) -> Optional[dict]:
    """Atomically claim a job from the pending queue."""
    job_id = await redis_client.lpop(f"jobs:{service_type}:pending")
    if not job_id:
        return None

    job_data = await redis_client.hgetall(f"agent_job:{job_id}")
    if not job_data or "job_id" not in job_data:
        return None

    await redis_client.set(
        f"jobs:{service_type}:assigned:{job_id}", agent_id, ex=AGENT_JOB_TTL_SECONDS
    )

    await redis_client.hset(f"agent_job:{job_id}", "status", "assigned")
    await redis_client.hset(f"agent_job:{job_id}", "assigned_to", agent_id)
    await redis_client.hset(f"agent_job:{job_id}", "assigned_at", str(time.time()))

    return {
        "job_id": job_data["job_id"],
        "service_type": job_data["service_type"],
        "payload": json.loads(job_data.get("payload", "{}")),
        "created_at": float(job_data["created_at"]),
    }


async def complete_agent_job(
    job_id: str,
    agent_id: str,
    status: str,
    result: Optional[dict],
    error: Optional[str],
) -> bool:
    """Mark a job as completed or failed."""
    job_key = f"agent_job:{job_id}"
    job_data = await redis_client.hgetall(job_key)

    if not job_data:
        return False

    service_type = job_data.get("service_type", "")
    assigned_to = job_data.get("assigned_to", "")
    if assigned_to != agent_id:
        return False

    updates = {
        "status": status,
        "completed_at": str(time.time()),
    }
    if result:
        updates["result"] = json.dumps(result)
    if error:
        updates["error"] = error

    await redis_client.hset(job_key, mapping=updates)
    await redis_client.expire(job_key, JOB_EXPIRY_SECONDS)
    await redis_client.delete(f"jobs:{service_type}:assigned:{job_id}")

    return True


async def get_agent_job_result(job_id: str) -> Optional[dict]:
    """Get the result of an agent job."""
    job_data = await redis_client.hgetall(f"agent_job:{job_id}")
    if not job_data:
        return None

    return {
        "job_id": job_data.get("job_id"),
        "status": job_data.get("status"),
        "result": json.loads(job_data.get("result", "null")),
        "error": job_data.get("error"),
        "payload": json.loads(job_data.get("payload", "{}")),
    }


async def submit_tts_job_via_agent(text: str, speaker: str) -> str:
    """Submit TTS job via agent system."""
    payload = {
        "text": text,
        "speaker": speaker,
    }
    return await create_agent_job("tts", payload)


async def poll_agent_job_result(
    job_id: str, service_type: str, max_wait_seconds: int = 600
) -> dict:
    """Poll for agent job completion. Returns result dict with 'audio' key for TTS jobs."""
    poll_interval = 2.0
    max_attempts = int(max_wait_seconds / poll_interval)
    attempts = 0

    while attempts < max_attempts:
        await asyncio.sleep(poll_interval)
        attempts += 1

        result = await get_agent_job_result(job_id)
        if not result:
            raise Exception(f"{service_type} job not found")

        status = result.get("status")

        if status == "completed":
            return result.get("result", {})
        elif status == "failed":
            raise Exception(
                f"{service_type} failed: {result.get('error', 'Unknown error')}"
            )
        elif status in ("pending", "assigned"):
            if attempts % 30 == 0:
                in_queue = await redis_client.lpos(
                    f"jobs:{service_type}:pending", job_id
                )
                if in_queue is None:
                    job_data = await redis_client.hgetall(f"agent_job:{job_id}")
                    if job_data and job_data.get("status") == "pending":
                        await redis_client.rpush(f"jobs:{service_type}:pending", job_id)

    raise Exception(f"{service_type} job timed out")


async def recover_expired_agent_jobs():
    """Background task to recover jobs from dead agents."""
    while True:
        await asyncio.sleep(60)

        try:
            cursor = 0
            while True:
                cursor, keys = await redis_client.scan(
                    cursor, match="agent_job:*", count=100
                )

                for key in keys:
                    job_data = await redis_client.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status != "assigned":
                        continue

                    job_id = job_data.get("job_id")
                    service_type = job_data.get("service_type")

                    assigned = await redis_client.exists(
                        f"jobs:{service_type}:assigned:{job_id}"
                    )
                    if not assigned:
                        await redis_client.hset(key, "status", "pending")
                        await redis_client.hdel(key, "assigned_to", "assigned_at")
                        await redis_client.rpush(f"jobs:{service_type}:pending", job_id)

                if cursor == 0:
                    break
        except Exception as e:
            print(f"Error in job recovery task: {e}")


async def timeout_stale_jobs():
    """Background task to mark jobs as failed if they don't complete in time."""
    while True:
        await asyncio.sleep(60)

        try:
            now = time.time()

            # Timeout agent jobs (TTS jobs etc)
            cursor = 0
            while True:
                cursor, keys = await redis_client.scan(
                    cursor, match="agent_job:*", count=100
                )

                for key in keys:
                    job_data = await redis_client.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status not in ("pending", "assigned"):
                        continue

                    created_at = float(job_data.get("created_at", 0))
                    if now - created_at > AGENT_JOB_TIMEOUT_SECONDS:
                        job_id = job_data.get("job_id")
                        if status == "pending":
                            error_msg = "No agents available to process job"
                        else:
                            error_msg = f"Job timed out after {AGENT_JOB_TIMEOUT_MINUTES} minutes"
                        print(
                            f"Timing out agent job {job_id} ({status}, age: {int(now - created_at)}s)"
                        )
                        await redis_client.hset(
                            key,
                            mapping={
                                "status": "failed",
                                "error": error_msg,
                            },
                        )

                if cursor == 0:
                    break

            # Timeout main jobs (prompt jobs, speak jobs)
            cursor = 0
            while True:
                cursor, keys = await redis_client.scan(cursor, match="job:*", count=100)

                for key in keys:
                    job_data = await redis_client.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status not in ("pending", "processing"):
                        continue

                    created_at = float(job_data.get("created_at", 0))
                    if now - created_at > AGENT_JOB_TIMEOUT_SECONDS:
                        job_id = job_data.get("job_id")
                        if status == "pending":
                            error_msg = "No agents available to process job"
                        else:
                            error_msg = f"Job timed out after {AGENT_JOB_TIMEOUT_MINUTES} minutes"
                        print(
                            f"Timing out job {job_id} ({status}, age: {int(now - created_at)}s)"
                        )
                        await redis_client.hmset(
                            key,
                            {
                                "status": "failed",
                                "error": error_msg,
                            },
                        )

                if cursor == 0:
                    break
        except Exception as e:
            print(f"Error in job timeout task: {e}")


async def cleanup_completed_jobs():
    """Background task to delete old completed/failed jobs."""
    while True:
        await asyncio.sleep(300)

        try:
            now = time.time()
            deleted_count = 0

            # Cleanup agent jobs
            cursor = 0
            while True:
                cursor, keys = await redis_client.scan(
                    cursor, match="agent_job:*", count=100
                )

                for key in keys:
                    job_data = await redis_client.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status not in ("completed", "failed"):
                        continue

                    created_at = float(job_data.get("created_at", 0))
                    if now - created_at > COMPLETED_JOB_RETENTION_SECONDS:
                        await redis_client.delete(key)
                        deleted_count += 1

                if cursor == 0:
                    break

            # Cleanup main jobs
            cursor = 0
            while True:
                cursor, keys = await redis_client.scan(cursor, match="job:*", count=100)

                for key in keys:
                    job_data = await redis_client.hgetall(key)
                    if not job_data:
                        continue

                    status = job_data.get("status")
                    if status not in ("completed", "failed"):
                        continue

                    created_at = float(job_data.get("created_at", 0))
                    if now - created_at > COMPLETED_JOB_RETENTION_SECONDS:
                        await redis_client.delete(key)
                        deleted_count += 1

                if cursor == 0:
                    break

            if deleted_count > 0:
                print(f"Cleaned up {deleted_count} old completed/failed jobs")
        except Exception as e:
            print(f"Error in job cleanup task: {e}")


async def process_prompt_job(job_id: str):
    try:
        job = await get_job(job_id)
        if not job:
            return

        await update_job_status(job_id, "processing")

        if not job.speaker:
            system_prompt = f"{FORMATTING_PREPROMPT}\n\n{PERSONALITY_PREPROMPT}"
            response_text = await call_openai(system_prompt, job.prompt)
            await update_job_status(
                job_id,
                "completed",
                completed_at=time.time(),
                response_text=response_text,
            )
            return

        voice = job.speaker_voice or DEFAULT_VOICE
        if not voice:
            await update_job_status(
                job_id,
                "failed",
                completed_at=time.time(),
                error="No voice specified and DEFAULT_VOICE not configured",
            )
            return

        display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{PERSONALITY_PREPROMPT}"
        display_response = await call_openai(display_system_prompt, job.prompt)

        tts_text = await call_openai(
            TTS_CONVERSION_PREPROMPT, display_response, temperature=0.1
        )

        tts_job_id = await submit_tts_job_via_agent(tts_text, voice)
        result = await poll_agent_job_result(tts_job_id, "tts")
        audio_base64 = result.get("audio")

        await update_job_status(
            job_id,
            "completed",
            completed_at=time.time(),
            response_text=display_response,
            audio_base64=audio_base64,
        )

    except Exception as e:
        await update_job_status(
            job_id, "failed", completed_at=time.time(), error=str(e)
        )


async def process_stream_job(job_id: str):
    """Process a streaming TTS job - generates audio for each sentence in parallel."""
    try:
        job = await get_stream_job(job_id)
        if not job:
            return

        await update_stream_job_status(job_id, "processing")

        voice = job["voice"]
        sentences = job["sentences"]
        pause_ms = job["pause_ms"]

        display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{PERSONALITY_PREPROMPT}"
        display_response = await call_openai(display_system_prompt, job["prompt"])
        
        await update_stream_job_status(job_id, "processing", response_text=display_response)

        tts_text = await call_openai(
            TTS_CONVERSION_PREPROMPT, display_response, temperature=0.1
        )

        tts_sentences = split_into_sentences(tts_text)
        
        if len(tts_sentences) != len(sentences):
            for i in range(len(sentences)):
                sentence_key = f"stream_job:{job_id}:sentence:{i}"
                await redis_client.delete(sentence_key)
            
            await redis_client.hset(f"stream_job:{job_id}", "sentence_count", str(len(tts_sentences)))
            
            for i, sentence in enumerate(tts_sentences):
                sentence_key = f"stream_job:{job_id}:sentence:{i}"
                sentence_data = {
                    "index": str(i),
                    "text": sentence,
                    "audio": "",
                    "status": "pending",
                    "tts_job_id": "",
                }
                await redis_client.hset(sentence_key, mapping=sentence_data)
                await redis_client.expire(sentence_key, JOB_EXPIRY_SECONDS * 2)

        async def process_sentence(index: int, text: str):
            try:
                tts_job_id = await submit_tts_job_via_agent(text, voice)
                await update_stream_sentence(job_id, index, status="processing", tts_job_id=tts_job_id)
                
                result = await poll_agent_job_result(tts_job_id, "tts")
                audio_base64 = result.get("audio", "")
                
                await update_stream_sentence(job_id, index, status="completed", audio=audio_base64)
                return audio_base64
            except Exception as e:
                await update_stream_sentence(job_id, index, status="failed")
                raise e

        tasks = [process_sentence(i, s) for i, s in enumerate(tts_sentences)]
        audio_results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = [r for r in audio_results if isinstance(r, Exception)]
        if errors:
            raise errors[0]

        audio_segments = []
        for audio_b64 in audio_results:
            if audio_b64:
                audio_segments.append(base64.b64decode(audio_b64))

        if audio_segments:
            combined_audio = combine_wav_audio(audio_segments, pause_ms)
            combined_audio_b64 = base64.b64encode(combined_audio).decode('utf-8')
        else:
            combined_audio_b64 = ""

        await update_stream_job_status(
            job_id,
            "completed",
            combined_audio=combined_audio_b64,
        )

    except Exception as e:
        await update_stream_job_status(job_id, "failed", error=str(e))


async def process_conversation_job(job_id: str):
    """Process a conversation job - generates response from conversation history."""
    try:
        job = await get_conversation_job(job_id)
        if not job:
            return

        await update_conversation_job_status(job_id, "processing")

        messages = json.loads(job.messages)
        
        # Enforce rolling window limit
        if len(messages) > MAX_CONVERSATION_MESSAGES:
            messages = messages[-MAX_CONVERSATION_MESSAGES:]

        # Convert to OpenAI format
        openai_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

        if not job.speaker:
            system_prompt = f"{FORMATTING_PREPROMPT}\n\n{PERSONALITY_PREPROMPT}"
            response_text = await call_openai_conversation(system_prompt, openai_messages)
            await update_conversation_job_status(
                job_id,
                "completed",
                completed_at=time.time(),
                response_text=response_text,
            )
            return

        voice = job.speaker_voice or DEFAULT_VOICE
        if not voice:
            await update_conversation_job_status(
                job_id,
                "failed",
                completed_at=time.time(),
                error="No voice specified and DEFAULT_VOICE not configured",
            )
            return

        display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{PERSONALITY_PREPROMPT}"
        display_response = await call_openai_conversation(display_system_prompt, openai_messages)

        tts_text = await call_openai(
            TTS_CONVERSION_PREPROMPT, display_response, temperature=0.1
        )

        tts_job_id = await submit_tts_job_via_agent(tts_text, voice)
        result = await poll_agent_job_result(tts_job_id, "tts")
        audio_base64 = result.get("audio")

        await update_conversation_job_status(
            job_id,
            "completed",
            completed_at=time.time(),
            response_text=display_response,
            audio_base64=audio_base64,
        )

    except Exception as e:
        await update_conversation_job_status(
            job_id, "failed", completed_at=time.time(), error=str(e)
        )


async def process_conversation_stream_job(job_id: str):
    """Process a conversation streaming TTS job - generates audio for each sentence in parallel."""
    try:
        job = await get_conversation_stream_job(job_id)
        if not job:
            return

        await update_conversation_stream_job_status(job_id, "processing")

        voice = job["voice"]
        sentences = job["sentences"]
        pause_ms = job["pause_ms"]
        messages = json.loads(job["messages"])
        
        # Enforce rolling window limit
        if len(messages) > MAX_CONVERSATION_MESSAGES:
            messages = messages[-MAX_CONVERSATION_MESSAGES:]

        # Convert to OpenAI format
        openai_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

        display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{PERSONALITY_PREPROMPT}"
        display_response = await call_openai_conversation(display_system_prompt, openai_messages)
        
        await update_conversation_stream_job_status(job_id, "processing", response_text=display_response)

        tts_text = await call_openai(
            TTS_CONVERSION_PREPROMPT, display_response, temperature=0.1
        )

        tts_sentences = split_into_sentences(tts_text)
        
        if len(tts_sentences) != len(sentences):
            for i in range(len(sentences)):
                sentence_key = f"conversation_stream_job:{job_id}:sentence:{i}"
                await redis_client.delete(sentence_key)
            
            await redis_client.hset(f"conversation_stream_job:{job_id}", "sentence_count", str(len(tts_sentences)))
            
            for i, sentence in enumerate(tts_sentences):
                sentence_key = f"conversation_stream_job:{job_id}:sentence:{i}"
                sentence_data = {
                    "index": str(i),
                    "text": sentence,
                    "audio": "",
                    "status": "pending",
                    "tts_job_id": "",
                }
                await redis_client.hset(sentence_key, mapping=sentence_data)
                await redis_client.expire(sentence_key, JOB_EXPIRY_SECONDS * 2)

        async def process_sentence(index: int, text: str):
            try:
                tts_job_id = await submit_tts_job_via_agent(text, voice)
                await update_conversation_stream_sentence(job_id, index, status="processing", tts_job_id=tts_job_id)
                
                result = await poll_agent_job_result(tts_job_id, "tts")
                audio_base64 = result.get("audio", "")
                
                await update_conversation_stream_sentence(job_id, index, status="completed", audio=audio_base64)
                return audio_base64
            except Exception as e:
                await update_conversation_stream_sentence(job_id, index, status="failed")
                raise e

        tasks = [process_sentence(i, s) for i, s in enumerate(tts_sentences)]
        audio_results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = [r for r in audio_results if isinstance(r, Exception)]
        if errors:
            raise errors[0]

        audio_segments = []
        for audio_b64 in audio_results:
            if audio_b64:
                audio_segments.append(base64.b64decode(audio_b64))

        if audio_segments:
            combined_audio = combine_wav_audio(audio_segments, pause_ms)
            combined_audio_b64 = base64.b64encode(combined_audio).decode('utf-8')
        else:
            combined_audio_b64 = ""

        await update_conversation_stream_job_status(
            job_id,
            "completed",
            combined_audio=combined_audio_b64,
        )

    except Exception as e:
        await update_conversation_stream_job_status(job_id, "failed", error=str(e))


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    response: Optional[str] = None
    audio: Optional[str] = None
    error: Optional[str] = None
    voice: Optional[str] = None


@app.post("/api/prompt", response_model=PromptResponse)
async def prompt(
    request: PromptRequest,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    if len(request.prompt) > MAX_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Prompt exceeds maximum length of {MAX_PROMPT_LENGTH} characters",
        )

    if not request.speaker:
        system_prompt = f"{FORMATTING_PREPROMPT}\n\n{PERSONALITY_PREPROMPT}"
        response_text = await call_openai(system_prompt, request.prompt)
        return PromptResponse(response=response_text)

    voice = request.speaker_voice or DEFAULT_VOICE
    if not voice:
        raise HTTPException(
            status_code=400,
            detail="No voice specified and DEFAULT_VOICE not configured",
        )

    display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{PERSONALITY_PREPROMPT}"
    display_response = await call_openai(display_system_prompt, request.prompt)

    tts_text = await call_openai(
        TTS_CONVERSION_PREPROMPT, display_response, temperature=0.1
    )

    tts_job_id = await submit_tts_job_via_agent(tts_text, voice)
    result = await poll_agent_job_result(tts_job_id, "tts")
    audio_base64 = result.get("audio")

    return PromptResponse(response=display_response, audio=audio_base64)


@app.get("/api/voices")
async def voices(
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """Get available voices aggregated from active TTS agents."""
    all_voices = set()
    now = time.time()
    dead_threshold = AGENT_HEARTBEAT_INTERVAL * AGENT_MISSED_HEARTBEATS

    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor, match="agent:tts:*", count=100)

        for key in keys:
            data = await redis_client.hgetall(key)
            if not data:
                continue

            last_seen = float(data.get("last_seen", 0))
            if now - last_seen > dead_threshold:
                continue

            if data.get("speakers"):
                try:
                    speakers = json.loads(data["speakers"])
                    all_voices.update(speakers)
                except json.JSONDecodeError:
                    pass

        if cursor == 0:
            break

    return {"voices": sorted(all_voices)}


@app.post("/api/prompt/job", response_model=JobSubmitResponse, status_code=202)
async def submit_prompt_job(
    request: PromptRequest,
    background_tasks: BackgroundTasks,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    if len(request.prompt) > MAX_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Prompt exceeds maximum length of {MAX_PROMPT_LENGTH} characters",
        )

    job_id = str(uuid.uuid4())
    job = PromptJob(
        job_id=job_id,
        prompt=request.prompt,
        speaker=request.speaker,
        speaker_voice=request.speaker_voice,
    )
    await save_job(job)

    background_tasks.add_task(process_prompt_job, job_id)

    return JobSubmitResponse(job_id=job_id, status="pending")


@app.get("/api/prompt/job/{job_id}", response_model=JobStatusResponse)
async def get_prompt_job_status(
    job_id: str,
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
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


@app.post("/api/agents/register", response_model=AgentRegisterResponse)
async def register_agent(
    request: Request,
    body: AgentRegisterRequest,
):
    verify_agent_key(request)

    agent_key = f"agent:{body.service_type}:{body.agent_id}"
    mapping = {
        "agent_id": body.agent_id,
        "service_type": body.service_type,
        "registered_at": str(time.time()),
        "last_seen": str(time.time()),
    }
    if body.speakers is not None:
        mapping["speakers"] = json.dumps(body.speakers)

    await redis_client.hset(agent_key, mapping=mapping)
    await redis_client.expire(agent_key, AGENT_RETENTION_SECONDS)

    return AgentRegisterResponse(status="ok", message="Agent registered")


@app.post("/api/agents/jobs/poll", response_model=AgentPollResponse)
async def poll_agent_jobs(
    request: Request,
    body: AgentPollRequest,
):
    verify_agent_key(request)

    agent_key = f"agent:{body.service_type}:{body.agent_id}"
    await redis_client.hset(agent_key, "last_seen", str(time.time()))
    await redis_client.expire(agent_key, AGENT_RETENTION_SECONDS)

    job = await poll_agent_job(body.agent_id, body.service_type)

    if job:
        return AgentPollResponse(job=AgentJob(**job))
    return AgentPollResponse(job=None)


@app.post("/api/agents/jobs/{job_id}/complete", response_model=AgentCompleteResponse)
async def complete_agent_job_endpoint(
    request: Request,
    job_id: str,
    body: AgentCompleteRequest,
):
    verify_agent_key(request)

    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    success = await complete_agent_job(
        job_id=job_id,
        agent_id=body.agent_id,
        status=body.status,
        result=body.result,
        error=body.error,
    )

    if not success:
        raise HTTPException(
            status_code=404, detail="Job not found or not assigned to this agent"
        )

    return AgentCompleteResponse(status="ok")


@app.post("/api/agents/heartbeat", response_model=AgentHeartbeatResponse)
async def agent_heartbeat(
    request: Request,
    body: AgentHeartbeatRequest,
):
    verify_agent_key(request)

    agent_key = f"agent:{body.service_type}:{body.agent_id}"
    exists = await redis_client.exists(agent_key)

    if not exists:
        mapping = {
            "agent_id": body.agent_id,
            "service_type": body.service_type,
            "registered_at": str(time.time()),
            "last_seen": str(time.time()),
        }
        if body.speakers is not None:
            mapping["speakers"] = json.dumps(body.speakers)
        await redis_client.hset(agent_key, mapping=mapping)
    else:
        updates = {"last_seen": str(time.time())}
        if body.speakers is not None:
            updates["speakers"] = json.dumps(body.speakers)
        await redis_client.hset(agent_key, mapping=updates)

    await redis_client.expire(agent_key, AGENT_RETENTION_SECONDS)

    return AgentHeartbeatResponse(status="ok")


@app.get("/api/agents", response_model=AgentListResponse)
async def list_agents(
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    """List all registered agents with their status and capabilities."""
    agents = []
    now = time.time()
    dead_threshold = AGENT_HEARTBEAT_INTERVAL * AGENT_MISSED_HEARTBEATS

    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor, match="agent:*:*", count=100)

        for key in keys:
            data = await redis_client.hgetall(key)
            if not data or "agent_id" not in data:
                continue

            last_seen = float(data.get("last_seen", 0))
            seconds_since_seen = now - last_seen
            status = "dead" if seconds_since_seen > dead_threshold else "active"

            speakers = []
            if data.get("speakers"):
                try:
                    speakers = json.loads(data["speakers"])
                except json.JSONDecodeError:
                    pass

            agents.append(
                AgentInfo(
                    agent_id=data["agent_id"],
                    service_type=data.get("service_type", "unknown"),
                    registered_at=float(data.get("registered_at", 0)),
                    last_seen=last_seen,
                    status=status,
                    speakers=speakers,
                )
            )

        if cursor == 0:
            break

    return AgentListResponse(agents=agents)


@app.post("/api/speak")
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


@app.post("/api/speak/job", response_model=SpeakJobResponse, status_code=202)
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


@app.get("/api/speak/job/{job_id}", response_model=SpeakJobStatusResponse)
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


@app.post("/api/stream/job", response_model=StreamJobResponse, status_code=202)
async def submit_stream_job(
    request: Request,
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

    job_id = str(uuid.uuid4())
    await save_stream_job(job_id, body.prompt, voice, initial_sentences, pause_ms)

    background_tasks.add_task(process_stream_job, job_id)

    return StreamJobResponse(job_id=job_id, status="pending")


@app.get("/api/stream/job/{job_id}", response_model=StreamJobStatusResponse)
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


@app.post("/api/conversation/job", response_model=JobSubmitResponse, status_code=202)
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
            detail=f"Total conversation content exceeds maximum length",
        )

    job_id = str(uuid.uuid4())
    messages_json = json.dumps([{"role": m.role, "content": m.content} for m in messages])
    
    job = ConversationJob(
        job_id=job_id,
        messages=messages_json,
        speaker=request.speaker,
        speaker_voice=request.speaker_voice,
    )
    await save_conversation_job(job)

    background_tasks.add_task(process_conversation_job, job_id)

    return JobSubmitResponse(job_id=job_id, status="pending")


@app.get("/api/conversation/job/{job_id}", response_model=ConversationJobStatusResponse)
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


@app.post("/api/conversation/stream/job", response_model=StreamJobResponse, status_code=202)
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
            detail=f"Total conversation content exceeds maximum length",
        )

    voice = request.speaker_voice or DEFAULT_VOICE
    if not voice:
        raise HTTPException(
            status_code=400,
            detail="No voice specified and DEFAULT_VOICE not configured",
        )

    pause_ms = request.sentence_pause_ms if request.sentence_pause_ms is not None else STREAM_SENTENCE_PAUSE_MS
    messages_json = json.dumps([{"role": m.role, "content": m.content} for m in messages])

    initial_sentences = ["Processing..."]

    job_id = str(uuid.uuid4())
    await save_conversation_stream_job(job_id, messages_json, voice, initial_sentences, pause_ms)

    background_tasks.add_task(process_conversation_stream_job, job_id)

    return StreamJobResponse(job_id=job_id, status="pending")


@app.get("/api/conversation/stream/job/{job_id}", response_model=StreamJobStatusResponse)
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


FORMAT_TEXT_PREPROMPT = """You are a text formatting assistant. Your job is to clean up speech-to-text output.

Rules:
- Add proper punctuation (periods, commas, question marks, exclamation points)
- Fix obvious grammar errors
- Capitalize the first letter of sentences and proper nouns
- Do NOT change the meaning or add new content
- Do NOT rephrase or summarize
- Keep the text as close to the original as possible
- Output only the formatted text, nothing else"""


SUMMARIZE_PREPROMPT = """Generate a very short title (maximum {max_words} words) that summarizes this text.

Rules:
- Maximum {max_words} words
- No punctuation at the end
- Capture the main topic or question
- Be concise and clear
- Output only the title, nothing else"""


@app.post("/api/format/text", response_model=FormatTextResponse)
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


@app.post("/api/summarize", response_model=SummarizeResponse)
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


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5003)
