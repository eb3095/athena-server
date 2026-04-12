"""Job storage - Redis CRUD operations for all job types."""

import json
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from athena.config import JOB_EXPIRY_SECONDS, STREAM_SENTENCE_PAUSE_MS
from athena.core.redis import get_redis
from athena.models.domain import ConversationJob, CouncilJob, PromptJob


# Prompt jobs
async def save_job(job: PromptJob):
    """Save a prompt job to Redis."""
    redis = get_redis()
    key = f"job:{job.job_id}"
    data = {}
    for k, v in asdict(job).items():
        if v is None:
            data[k] = ""
        elif isinstance(v, bool):
            data[k] = "1" if v else "0"
        else:
            data[k] = str(v)
    await redis.hset(key, mapping=data)
    ttl = (
        JOB_EXPIRY_SECONDS
        if job.status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis.expire(key, ttl)


async def get_job(job_id: str) -> Optional[PromptJob]:
    """Get a prompt job from Redis."""
    redis = get_redis()
    key = f"job:{job_id}"
    data = await redis.hgetall(key)
    if not data or "job_id" not in data:
        return None
    return PromptJob(
        job_id=data["job_id"],
        prompt=data.get("prompt", ""),
        speaker=data.get("speaker") == "1",
        speaker_voice=data["speaker_voice"] if data.get("speaker_voice") else None,
        personality_prompt=data.get("personality_prompt") if data.get("personality_prompt") else None,
        status=data.get("status", "failed"),
        created_at=float(data["created_at"]) if data.get("created_at") else 0.0,
        completed_at=float(data["completed_at"]) if data.get("completed_at") else None,
        response_text=data.get("response_text") if data.get("response_text") else None,
        audio_base64=data.get("audio_base64") if data.get("audio_base64") else None,
        error=data.get("error") if data.get("error") else None,
    )


async def update_job_status(job_id: str, status: str, **fields):
    """Update prompt job status."""
    redis = get_redis()
    key = f"job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis.expire(key, ttl)


# Stream jobs
async def save_stream_job(
    job_id: str,
    prompt: str,
    voice: str,
    sentences: List[str],
    pause_ms: int,
    personality_prompt: Optional[str] = None,
):
    """Save a streaming job with its sentences."""
    redis = get_redis()
    key = f"stream_job:{job_id}"
    data = {
        "job_id": job_id,
        "prompt": prompt,
        "voice": voice,
        "status": "pending",
        "pause_ms": str(pause_ms),
        "personality_prompt": personality_prompt or "",
        "created_at": str(time.time()),
        "sentence_count": str(len(sentences)),
        "response_text": "",
        "combined_audio": "",
        "error": "",
    }
    await redis.hset(key, mapping=data)
    await redis.expire(key, JOB_EXPIRY_SECONDS * 2)

    for i, sentence in enumerate(sentences):
        sentence_key = f"stream_job:{job_id}:sentence:{i}"
        sentence_data = {
            "index": str(i),
            "text": sentence,
            "audio": "",
            "status": "pending",
            "tts_job_id": "",
        }
        await redis.hset(sentence_key, mapping=sentence_data)
        await redis.expire(sentence_key, JOB_EXPIRY_SECONDS * 2)


async def get_stream_job(job_id: str) -> Optional[dict]:
    """Get a streaming job with all its sentences."""
    redis = get_redis()
    key = f"stream_job:{job_id}"
    data = await redis.hgetall(key)
    if not data or "job_id" not in data:
        return None

    sentence_count = int(data.get("sentence_count", 0))
    sentences = []

    for i in range(sentence_count):
        sentence_key = f"stream_job:{job_id}:sentence:{i}"
        sentence_data = await redis.hgetall(sentence_key)
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
        "personality_prompt": data.get("personality_prompt") or None,
        "response_text": data.get("response_text") or None,
        "combined_audio": data.get("combined_audio") or None,
        "error": data.get("error") or None,
        "sentences": sentences,
    }


async def update_stream_job_status(job_id: str, status: str, **fields):
    """Update streaming job status."""
    redis = get_redis()
    key = f"stream_job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis.expire(key, ttl)


async def update_stream_sentence(job_id: str, index: int, **fields):
    """Update a sentence's status/audio in a streaming job."""
    redis = get_redis()
    key = f"stream_job:{job_id}:sentence:{index}"
    updates = {}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    await redis.expire(key, JOB_EXPIRY_SECONDS * 2)


# Conversation jobs
async def save_conversation_job(job: ConversationJob):
    """Save a conversation job to Redis."""
    redis = get_redis()
    key = f"conversation_job:{job.job_id}"
    data = {}
    for k, v in asdict(job).items():
        if v is None:
            data[k] = ""
        elif isinstance(v, bool):
            data[k] = "1" if v else "0"
        else:
            data[k] = str(v)
    await redis.hset(key, mapping=data)
    ttl = (
        JOB_EXPIRY_SECONDS
        if job.status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis.expire(key, ttl)


async def get_conversation_job(job_id: str) -> Optional[ConversationJob]:
    """Get a conversation job from Redis."""
    redis = get_redis()
    key = f"conversation_job:{job_id}"
    data = await redis.hgetall(key)
    if not data or "job_id" not in data:
        return None
    return ConversationJob(
        job_id=data["job_id"],
        messages=data.get("messages", "[]"),
        speaker=data.get("speaker") == "1",
        speaker_voice=data["speaker_voice"] if data.get("speaker_voice") else None,
        personality_prompt=data.get("personality_prompt") if data.get("personality_prompt") else None,
        status=data.get("status", "failed"),
        created_at=float(data["created_at"]) if data.get("created_at") else 0.0,
        completed_at=float(data["completed_at"]) if data.get("completed_at") else None,
        response_text=data.get("response_text") if data.get("response_text") else None,
        audio_base64=data.get("audio_base64") if data.get("audio_base64") else None,
        error=data.get("error") if data.get("error") else None,
    )


async def update_conversation_job_status(job_id: str, status: str, **fields):
    """Update conversation job status."""
    redis = get_redis()
    key = f"conversation_job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis.expire(key, ttl)


# Conversation stream jobs
async def save_conversation_stream_job(
    job_id: str,
    messages: str,
    voice: str,
    sentences: List[str],
    pause_ms: int,
    personality_prompt: Optional[str] = None,
):
    """Save a conversation streaming job with its sentences."""
    redis = get_redis()
    key = f"conversation_stream_job:{job_id}"
    data = {
        "job_id": job_id,
        "messages": messages,
        "voice": voice,
        "status": "pending",
        "pause_ms": str(pause_ms),
        "personality_prompt": personality_prompt or "",
        "created_at": str(time.time()),
        "sentence_count": str(len(sentences)),
        "response_text": "",
        "combined_audio": "",
        "error": "",
    }
    await redis.hset(key, mapping=data)
    await redis.expire(key, JOB_EXPIRY_SECONDS * 2)

    for i, sentence in enumerate(sentences):
        sentence_key = f"conversation_stream_job:{job_id}:sentence:{i}"
        sentence_data = {
            "index": str(i),
            "text": sentence,
            "audio": "",
            "status": "pending",
            "tts_job_id": "",
        }
        await redis.hset(sentence_key, mapping=sentence_data)
        await redis.expire(sentence_key, JOB_EXPIRY_SECONDS * 2)


async def get_conversation_stream_job(job_id: str) -> Optional[dict]:
    """Get a conversation streaming job with all its sentences."""
    redis = get_redis()
    key = f"conversation_stream_job:{job_id}"
    data = await redis.hgetall(key)
    if not data or "job_id" not in data:
        return None

    sentence_count = int(data.get("sentence_count", 0))
    sentences = []

    for i in range(sentence_count):
        sentence_key = f"conversation_stream_job:{job_id}:sentence:{i}"
        sentence_data = await redis.hgetall(sentence_key)
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
        "personality_prompt": data.get("personality_prompt") or None,
        "response_text": data.get("response_text") or None,
        "combined_audio": data.get("combined_audio") or None,
        "error": data.get("error") or None,
        "sentences": sentences,
    }


async def update_conversation_stream_job_status(job_id: str, status: str, **fields):
    """Update conversation streaming job status."""
    redis = get_redis()
    key = f"conversation_stream_job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis.expire(key, ttl)


async def update_conversation_stream_sentence(job_id: str, index: int, **fields):
    """Update a sentence's status/audio in a conversation streaming job."""
    redis = get_redis()
    key = f"conversation_stream_job:{job_id}:sentence:{index}"
    updates = {}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    await redis.expire(key, JOB_EXPIRY_SECONDS * 2)


# Council jobs
async def save_council_job(job: CouncilJob):
    """Save a council job to Redis."""
    redis = get_redis()
    key = f"council_job:{job.job_id}"
    data = {}
    for k, v in asdict(job).items():
        if v is None:
            data[k] = ""
        elif isinstance(v, bool):
            data[k] = "1" if v else "0"
        else:
            data[k] = str(v)
    await redis.hset(key, mapping=data)
    ttl = (
        JOB_EXPIRY_SECONDS
        if job.status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis.expire(key, ttl)


async def get_council_job(job_id: str) -> Optional[CouncilJob]:
    """Get a council job from Redis."""
    redis = get_redis()
    key = f"council_job:{job_id}"
    data = await redis.hgetall(key)
    if not data or "job_id" not in data:
        return None
    return CouncilJob(
        job_id=data["job_id"],
        messages=data.get("messages", "[]"),
        council_members=data.get("council_members", "[]"),
        user_traits=data.get("user_traits", "[]"),
        user_goal=data.get("user_goal", ""),
        speaker_voice=data["speaker_voice"] if data.get("speaker_voice") else None,
        status=data.get("status", "pending"),
        phase=data.get("phase", "initial"),
        created_at=float(data["created_at"]) if data.get("created_at") else 0.0,
        completed_at=float(data["completed_at"]) if data.get("completed_at") else None,
        advisor_response=data.get("advisor_response") if data.get("advisor_response") else None,
        member_responses=data.get("member_responses") if data.get("member_responses") else None,
        audio_base64=data.get("audio_base64") if data.get("audio_base64") else None,
        error=data.get("error") if data.get("error") else None,
    )


async def update_council_job_status(job_id: str, status: str, **fields):
    """Update council job status."""
    redis = get_redis()
    key = f"council_job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis.expire(key, ttl)


async def save_council_member_data(job_id: str, member_name: str, data: Dict[str, Any]):
    """Save per-member data for a council job."""
    redis = get_redis()
    key = f"council_job:{job_id}:member:{member_name}"
    str_data = {}
    for k, v in data.items():
        if v is None:
            str_data[k] = ""
        elif isinstance(v, (list, dict)):
            str_data[k] = json.dumps(v)
        else:
            str_data[k] = str(v)
    await redis.hset(key, mapping=str_data)
    await redis.expire(key, JOB_EXPIRY_SECONDS * 2)


async def get_council_member_data(job_id: str, member_name: str) -> Optional[Dict[str, Any]]:
    """Get per-member data for a council job."""
    redis = get_redis()
    key = f"council_job:{job_id}:member:{member_name}"
    data = await redis.hgetall(key)
    if not data:
        return None
    
    result = {}
    for k, v in data.items():
        if k in ("notes_received",):
            try:
                result[k] = json.loads(v) if v else []
            except json.JSONDecodeError:
                result[k] = []
        else:
            result[k] = v if v else None
    return result


async def update_council_member_data(job_id: str, member_name: str, **fields):
    """Update per-member data for a council job."""
    redis = get_redis()
    key = f"council_job:{job_id}:member:{member_name}"
    updates = {}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        elif isinstance(v, (list, dict)):
            updates[k] = json.dumps(v)
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    await redis.expire(key, JOB_EXPIRY_SECONDS * 2)


# Council stream jobs
async def save_council_stream_job(
    job_id: str,
    messages: str,
    council_members: str,
    user_traits: str,
    user_goal: str,
    voice: str,
    sentences: List[str],
    pause_ms: int,
):
    """Save a council streaming job with its sentences."""
    redis = get_redis()
    key = f"council_stream_job:{job_id}"
    data = {
        "job_id": job_id,
        "messages": messages,
        "council_members": council_members,
        "user_traits": user_traits,
        "user_goal": user_goal,
        "voice": voice,
        "status": "pending",
        "phase": "initial",
        "pause_ms": str(pause_ms),
        "created_at": str(time.time()),
        "sentence_count": str(len(sentences)),
        "advisor_response": "",
        "member_responses": "",
        "combined_audio": "",
        "error": "",
    }
    await redis.hset(key, mapping=data)
    await redis.expire(key, JOB_EXPIRY_SECONDS * 2)

    for i, sentence in enumerate(sentences):
        sentence_key = f"council_stream_job:{job_id}:sentence:{i}"
        sentence_data = {
            "index": str(i),
            "text": sentence,
            "audio": "",
            "status": "pending",
            "tts_job_id": "",
        }
        await redis.hset(sentence_key, mapping=sentence_data)
        await redis.expire(sentence_key, JOB_EXPIRY_SECONDS * 2)


async def get_council_stream_job(job_id: str) -> Optional[dict]:
    """Get a council streaming job with all its sentences."""
    redis = get_redis()
    key = f"council_stream_job:{job_id}"
    data = await redis.hgetall(key)
    if not data or "job_id" not in data:
        return None

    sentence_count = int(data.get("sentence_count", 0))
    sentences = []

    for i in range(sentence_count):
        sentence_key = f"council_stream_job:{job_id}:sentence:{i}"
        sentence_data = await redis.hgetall(sentence_key)
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
        "council_members": data.get("council_members", "[]"),
        "user_traits": data.get("user_traits", "[]"),
        "user_goal": data.get("user_goal", ""),
        "voice": data.get("voice", ""),
        "status": data.get("status", "pending"),
        "phase": data.get("phase", "initial"),
        "pause_ms": int(data.get("pause_ms", STREAM_SENTENCE_PAUSE_MS)),
        "advisor_response": data.get("advisor_response") or None,
        "member_responses": data.get("member_responses") or None,
        "combined_audio": data.get("combined_audio") or None,
        "error": data.get("error") or None,
        "sentences": sentences,
    }


async def update_council_stream_job_status(job_id: str, status: str, **fields):
    """Update council streaming job status."""
    redis = get_redis()
    key = f"council_stream_job:{job_id}"
    updates = {"status": status}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    ttl = (
        JOB_EXPIRY_SECONDS
        if status in ("completed", "failed")
        else JOB_EXPIRY_SECONDS * 2
    )
    await redis.expire(key, ttl)


async def update_council_stream_sentence(job_id: str, index: int, **fields):
    """Update a sentence's status/audio in a council streaming job."""
    redis = get_redis()
    key = f"council_stream_job:{job_id}:sentence:{index}"
    updates = {}
    for k, v in fields.items():
        if v is None:
            updates[k] = ""
        else:
            updates[k] = str(v)
    await redis.hset(key, mapping=updates)
    await redis.expire(key, JOB_EXPIRY_SECONDS * 2)
