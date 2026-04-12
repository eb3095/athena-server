"""Job processors - process_prompt_job, process_stream_job, etc."""

import asyncio
import base64
import json
import time

from athena.agents.tts import poll_agent_job_result, submit_tts_job_via_agent
from athena.audio.utils import combine_wav_audio, split_into_sentences
from athena.config import (
    DEFAULT_VOICE,
    FORMATTING_PREPROMPT,
    JOB_EXPIRY_SECONDS,
    MAX_CONVERSATION_MESSAGES,
    TTS_CONVERSION_PREPROMPT,
    get_personality,
)
from athena.core.openai import call_openai, call_openai_conversation
from athena.core.redis import get_redis
from athena.jobs.storage import (
    get_conversation_job,
    get_conversation_stream_job,
    get_job,
    get_stream_job,
    update_conversation_job_status,
    update_conversation_stream_job_status,
    update_conversation_stream_sentence,
    update_job_status,
    update_stream_job_status,
    update_stream_sentence,
)


async def process_prompt_job(job_id: str):
    """Process a prompt job."""
    try:
        job = await get_job(job_id)
        if not job:
            return

        await update_job_status(job_id, "processing")

        personality = job.personality_prompt or get_personality()

        if not job.speaker:
            system_prompt = f"{FORMATTING_PREPROMPT}\n\n{personality}"
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

        display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{personality}"
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
        redis = get_redis()
        job = await get_stream_job(job_id)
        if not job:
            return

        await update_stream_job_status(job_id, "processing")

        voice = job["voice"]
        sentences = job["sentences"]
        pause_ms = job["pause_ms"]
        personality = job.get("personality_prompt") or get_personality()

        display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{personality}"
        display_response = await call_openai(display_system_prompt, job["prompt"])

        await update_stream_job_status(job_id, "processing", response_text=display_response)

        tts_text = await call_openai(
            TTS_CONVERSION_PREPROMPT, display_response, temperature=0.1
        )

        tts_sentences = split_into_sentences(tts_text)

        if len(tts_sentences) != len(sentences):
            for i in range(len(sentences)):
                sentence_key = f"stream_job:{job_id}:sentence:{i}"
                await redis.delete(sentence_key)

            await redis.hset(f"stream_job:{job_id}", "sentence_count", str(len(tts_sentences)))

            for i, sentence in enumerate(tts_sentences):
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

        personality = job.personality_prompt or get_personality()

        messages = json.loads(job.messages)

        # Enforce rolling window limit
        if len(messages) > MAX_CONVERSATION_MESSAGES:
            messages = messages[-MAX_CONVERSATION_MESSAGES:]

        # Convert to OpenAI format
        openai_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

        if not job.speaker:
            system_prompt = f"{FORMATTING_PREPROMPT}\n\n{personality}"
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

        display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{personality}"
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
        redis = get_redis()
        job = await get_conversation_stream_job(job_id)
        if not job:
            return

        await update_conversation_stream_job_status(job_id, "processing")

        voice = job["voice"]
        sentences = job["sentences"]
        pause_ms = job["pause_ms"]
        personality = job.get("personality_prompt") or get_personality()
        messages = json.loads(job["messages"])

        # Enforce rolling window limit
        if len(messages) > MAX_CONVERSATION_MESSAGES:
            messages = messages[-MAX_CONVERSATION_MESSAGES:]

        # Convert to OpenAI format
        openai_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

        display_system_prompt = f"{FORMATTING_PREPROMPT}\n\n{personality}"
        display_response = await call_openai_conversation(display_system_prompt, openai_messages)

        await update_conversation_stream_job_status(job_id, "processing", response_text=display_response)

        tts_text = await call_openai(
            TTS_CONVERSION_PREPROMPT, display_response, temperature=0.1
        )

        tts_sentences = split_into_sentences(tts_text)

        if len(tts_sentences) != len(sentences):
            for i in range(len(sentences)):
                sentence_key = f"conversation_stream_job:{job_id}:sentence:{i}"
                await redis.delete(sentence_key)

            await redis.hset(f"conversation_stream_job:{job_id}", "sentence_count", str(len(tts_sentences)))

            for i, sentence in enumerate(tts_sentences):
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
