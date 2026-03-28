from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from openai import AsyncOpenAI
import uvicorn
import httpx
import base64
import os
import secrets
import time
from typing import Optional
from collections import defaultdict

openai_client: Optional[AsyncOpenAI] = None
http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global openai_client, http_client

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required")

    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    )

    if DEFAULT_VOICE:
        try:
            voices = await _get_voices_internal()
            if DEFAULT_VOICE not in voices:
                print(
                    f"WARNING: DEFAULT_VOICE '{DEFAULT_VOICE}' not found in athena-tts"
                )
        except Exception as e:
            print(f"WARNING: Could not validate DEFAULT_VOICE: {e}")

    yield

    if http_client:
        await http_client.aclose()


async def _get_voices_internal() -> list[str]:
    response = await http_client.get(
        f"{ATHENA_TTS_URL}/api/speakers",
        headers={"Authorization": f"Bearer {ATHENA_TTS_TOKEN}"},
        timeout=10.0,
    )
    if response.status_code != 200:
        return []
    return response.json().get("speakers", [])


app = FastAPI(lifespan=lifespan)
security = HTTPBearer()

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
OPENAI_TEMPERATURE = float(os.environ.get("OPENAI_TEMPERATURE", "0.7"))
OPENAI_MAX_TOKENS = int(os.environ.get("OPENAI_MAX_TOKENS", "4096"))

ATHENA_TTS_URL = os.environ.get("ATHENA_TTS_URL", "http://localhost:5002")
ATHENA_TTS_TOKEN = os.environ.get("ATHENA_TTS_TOKEN", "")
DEFAULT_VOICE = os.environ.get("DEFAULT_VOICE", "")

DEFAULT_FORMATTING_PREPROMPT = "Format your response using markdown when appropriate. Use headers, bullet points, code blocks, and emphasis to make the response clear and readable."
DEFAULT_PERSONALITY_PREPROMPT = "You are a helpful AI assistant."
DEFAULT_TTS_CONVERSION_PREPROMPT = "Rewrite this as natural speech. Keep the same meaning and information but make it sound like someone talking. No markdown, no bullet points, no formatting - just flowing sentences. Never say words like 'bullet', 'asterisk', 'heading', or 'code block'. Spell out abbreviations and numbers naturally. Output only the rewritten text."

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


async def call_openai(system_prompt: str, user_prompt: str) -> str:
    response = await openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=OPENAI_TEMPERATURE,
        max_tokens=OPENAI_MAX_TOKENS,
        timeout=OPENAI_TIMEOUT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content or ""


async def call_tts(text: str, speaker: str) -> bytes:
    response = await http_client.post(
        f"{ATHENA_TTS_URL}/api/tts",
        headers={"Authorization": f"Bearer {ATHENA_TTS_TOKEN}"},
        data={"text": text, "speaker": speaker},
        timeout=120.0,
    )
    if response.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"TTS service error: {response.text}"
        )
    return response.content


async def get_available_voices() -> list[str]:
    response = await http_client.get(
        f"{ATHENA_TTS_URL}/api/speakers",
        headers={"Authorization": f"Bearer {ATHENA_TTS_TOKEN}"},
        timeout=10.0,
    )
    if response.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"TTS service error: {response.text}"
        )
    return response.json().get("speakers", [])


async def validate_voice(voice: str) -> bool:
    voices = await get_available_voices()
    return voice in voices


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

    if not await validate_voice(voice):
        raise HTTPException(status_code=400, detail=f"Voice '{voice}' not found")

    display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{PERSONALITY_PREPROMPT}"
    display_response = await call_openai(display_system_prompt, request.prompt)

    tts_response = await call_openai(TTS_CONVERSION_PREPROMPT, display_response)

    audio_bytes = await call_tts(tts_response, voice)
    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    return PromptResponse(response=display_response, audio=audio_base64)


@app.get("/api/voices")
async def voices(
    _: HTTPAuthorizationCredentials = Depends(verify_token),
):
    voice_list = await get_available_voices()
    return {"voices": voice_list}


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5003)
