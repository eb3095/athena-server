"""Council job processors - 4-phase parallel advisory council processing."""

import asyncio
import base64
import json
import time
from typing import Any, Dict, List, Optional

from athena.agents.tts import poll_agent_job_result, submit_tts_job_via_agent
from athena.audio.utils import combine_wav_audio, split_into_sentences
from athena.config import (
    COUNCIL_ADVISOR_PROMPT,
    COUNCIL_FINAL_NOTE_PROMPT,
    COUNCIL_NOTE_PROMPT,
    COUNCIL_UNIVERSAL_PROMPT,
    DEFAULT_VOICE,
    FORMATTING_PREPROMPT,
    JOB_EXPIRY_SECONDS,
    MAX_CONVERSATION_MESSAGES,
    TTS_CONVERSION_PREPROMPT,
    get_user_context,
)
from athena.core.openai import call_openai, call_openai_conversation
from athena.core.redis import get_redis
from athena.jobs.storage import (
    get_council_job,
    get_council_member_data,
    get_council_stream_job,
    save_council_member_data,
    update_council_job_status,
    update_council_member_data,
    update_council_stream_job_status,
    update_council_stream_sentence,
)


def _safe_format(template: str, **kwargs) -> str:
    """Format a template string, ignoring missing placeholders."""
    try:
        return template.format(**kwargs)
    except KeyError:
        result = template
        for key, value in kwargs.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result


async def _get_initial_response(
    member: Dict[str, str],
    messages: List[Dict[str, str]],
    user_context: str,
) -> Dict[str, Any]:
    """Get initial response from a council member."""
    base_prompt = _safe_format(
        COUNCIL_UNIVERSAL_PROMPT,
        member_name=member["name"],
        member_prompt=member["prompt"],
        user_context=user_context,
    )
    
    prompt = f"""You are: {member["name"]}
Your perspective: {member["prompt"]}

{user_context}

{base_prompt}"""
    
    system_prompt = f"{FORMATTING_PREPROMPT}\n\n{prompt}"
    
    try:
        response = await call_openai_conversation(system_prompt, messages)
        return {
            "name": member["name"],
            "initial_response": response,
            "error": None,
        }
    except Exception as e:
        return {
            "name": member["name"],
            "initial_response": "",
            "error": str(e),
        }


async def _get_cross_note(
    member: Dict[str, str],
    other_name: str,
    other_response: str,
) -> Dict[str, Any]:
    """Get a note from one member about another's response."""
    base_prompt = _safe_format(
        COUNCIL_NOTE_PROMPT,
        member_name=member["name"],
        member_prompt=member["prompt"],
        other_name=other_name,
        other_response=other_response,
    )
    
    prompt = f"""You are: {member["name"]}
Your perspective: {member["prompt"]}

Another council member, {other_name}, provided this response:

{other_response}

{base_prompt}"""
    
    system_prompt = f"{FORMATTING_PREPROMPT}\n\n{prompt}"
    
    try:
        note = await call_openai(system_prompt, f"Provide your note on {other_name}'s response.")
        return {
            "from_member": member["name"],
            "to_member": other_name,
            "note": note,
            "error": None,
        }
    except Exception as e:
        return {
            "from_member": member["name"],
            "to_member": other_name,
            "note": "",
            "error": str(e),
        }


async def _get_final_note(
    member: Dict[str, str],
    initial_response: str,
    notes_received: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Get final note from a member after reviewing notes on their response."""
    notes_text = "\n\n".join([
        f"From {n['from_member']}:\n{n['note']}"
        for n in notes_received
        if n.get("note")
    ])
    
    if not notes_text:
        notes_text = "(No notes received)"
    
    base_prompt = _safe_format(
        COUNCIL_FINAL_NOTE_PROMPT,
        member_name=member["name"],
        member_prompt=member["prompt"],
        initial_response=initial_response,
        notes=notes_text,
    )
    
    prompt = f"""You are: {member["name"]}
Your perspective: {member["prompt"]}

You provided this initial response:

{initial_response}

Other council members provided these notes on your response:

{notes_text}

{base_prompt}"""
    
    system_prompt = f"{FORMATTING_PREPROMPT}\n\n{prompt}"
    
    try:
        final_note = await call_openai(system_prompt, "Provide your refined final note.")
        return {
            "name": member["name"],
            "final_note": final_note,
            "error": None,
        }
    except Exception as e:
        return {
            "name": member["name"],
            "final_note": "",
            "error": str(e),
        }


async def _synthesize_advisor_response(
    member_data: List[Dict[str, Any]],
    messages: List[Dict[str, str]],
    user_context: str,
) -> str:
    """Synthesize final advisor response from all council input."""
    council_summary = []
    for m in member_data:
        summary = "=== Council Member Input ===\n"
        summary += f"Initial perspective:\n{m.get('initial_response', '')}\n\n"
        
        notes = m.get("notes_received", [])
        if notes:
            summary += "Feedback received:\n"
            for n in notes:
                summary += f"- {n.get('note', '')}\n"
            summary += "\n"
        
        final = m.get("final_note", "")
        if final:
            summary += f"Refined position:\n{final}\n"
        
        council_summary.append(summary)
    
    base_prompt = _safe_format(COUNCIL_ADVISOR_PROMPT, user_context=user_context)
    
    council_input = "\n---\n".join(council_summary)
    
    prompt = f"""{base_prompt}

{user_context}

Council input to synthesize:

{council_input}"""
    
    system_prompt = f"{FORMATTING_PREPROMPT}\n\n{prompt}"
    
    response = await call_openai_conversation(system_prompt, messages)
    return response


async def process_council_job(job_id: str):
    """Process a council job through all 4 phases."""
    try:
        job = await get_council_job(job_id)
        if not job:
            return

        await update_council_job_status(job_id, "processing", phase="initial")

        messages = json.loads(job.messages)
        council_members = json.loads(job.council_members)
        user_traits = json.loads(job.user_traits) if job.user_traits else []
        user_goal = job.user_goal or ""
        
        if len(messages) > MAX_CONVERSATION_MESSAGES:
            messages = messages[-MAX_CONVERSATION_MESSAGES:]
        
        openai_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        user_context = get_user_context(user_traits, user_goal)

        # Phase 1: Initial responses (parallel)
        initial_tasks = [
            _get_initial_response(member, openai_messages, user_context)
            for member in council_members
        ]
        initial_results = await asyncio.gather(*initial_tasks)
        
        for result in initial_results:
            await save_council_member_data(job_id, result["name"], {
                "initial_response": result["initial_response"],
                "notes_received": [],
                "final_note": "",
            })
        
        errors = [r for r in initial_results if r.get("error")]
        if len(errors) == len(initial_results):
            raise Exception("All council members failed to respond")

        await update_council_job_status(job_id, "processing", phase="notes")

        # Phase 2: Cross-notes (parallel)
        note_tasks = []
        for member in council_members:
            for other in initial_results:
                if other["name"] != member["name"] and other["initial_response"]:
                    note_tasks.append(_get_cross_note(
                        member,
                        other["name"],
                        other["initial_response"],
                    ))
        
        note_results = await asyncio.gather(*note_tasks)
        
        notes_by_recipient: Dict[str, List[Dict[str, str]]] = {}
        for note in note_results:
            if note.get("note"):
                recipient = note["to_member"]
                if recipient not in notes_by_recipient:
                    notes_by_recipient[recipient] = []
                notes_by_recipient[recipient].append({
                    "from_member": note["from_member"],
                    "note": note["note"],
                })
        
        for member_name, notes in notes_by_recipient.items():
            await update_council_member_data(job_id, member_name, notes_received=notes)

        await update_council_job_status(job_id, "processing", phase="final_notes")

        # Phase 3: Final notes (parallel)
        final_tasks = []
        for result in initial_results:
            if not result.get("error"):
                member = next((m for m in council_members if m["name"] == result["name"]), None)
                if member:
                    notes = notes_by_recipient.get(result["name"], [])
                    final_tasks.append(_get_final_note(
                        member,
                        result["initial_response"],
                        notes,
                    ))
        
        final_results = await asyncio.gather(*final_tasks)
        
        for result in final_results:
            if result.get("final_note"):
                await update_council_member_data(
                    job_id, result["name"],
                    final_note=result["final_note"]
                )

        await update_council_job_status(job_id, "processing", phase="synthesis")

        # Phase 4: Advisor synthesis
        member_data = []
        for member in council_members:
            data = await get_council_member_data(job_id, member["name"])
            if data:
                member_data.append({
                    "name": member["name"],
                    "initial_response": data.get("initial_response", ""),
                    "notes_received": data.get("notes_received", []),
                    "final_note": data.get("final_note", ""),
                })
        
        advisor_response = await _synthesize_advisor_response(
            member_data, openai_messages, user_context
        )
        
        member_responses_json = json.dumps(member_data)

        if not job.speaker_voice:
            await update_council_job_status(
                job_id,
                "completed",
                completed_at=time.time(),
                advisor_response=advisor_response,
                member_responses=member_responses_json,
            )
            return

        await update_council_job_status(job_id, "processing", phase="tts")

        voice = job.speaker_voice or DEFAULT_VOICE
        if not voice:
            await update_council_job_status(
                job_id,
                "completed",
                completed_at=time.time(),
                advisor_response=advisor_response,
                member_responses=member_responses_json,
            )
            return

        tts_text = await call_openai(
            TTS_CONVERSION_PREPROMPT, advisor_response, temperature=0.1
        )

        tts_job_id = await submit_tts_job_via_agent(tts_text, voice)
        result = await poll_agent_job_result(tts_job_id, "tts")
        audio_base64 = result.get("audio")

        await update_council_job_status(
            job_id,
            "completed",
            completed_at=time.time(),
            advisor_response=advisor_response,
            member_responses=member_responses_json,
            audio_base64=audio_base64,
        )

    except Exception as e:
        await update_council_job_status(
            job_id, "failed", completed_at=time.time(), error=str(e)
        )


async def process_council_stream_job(job_id: str):
    """Process a council streaming job - council processing + sentence-by-sentence TTS."""
    try:
        redis = get_redis()
        job = await get_council_stream_job(job_id)
        if not job:
            return

        await update_council_stream_job_status(job_id, "processing", phase="initial")

        messages = json.loads(job["messages"])
        council_members = json.loads(job["council_members"])
        user_traits = json.loads(job["user_traits"]) if job.get("user_traits") else []
        user_goal = job.get("user_goal", "")
        voice = job["voice"]
        sentences = job["sentences"]
        pause_ms = job["pause_ms"]
        
        if len(messages) > MAX_CONVERSATION_MESSAGES:
            messages = messages[-MAX_CONVERSATION_MESSAGES:]
        
        openai_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        user_context = get_user_context(user_traits, user_goal)

        # Phase 1: Initial responses (parallel)
        initial_tasks = [
            _get_initial_response(member, openai_messages, user_context)
            for member in council_members
        ]
        initial_results = await asyncio.gather(*initial_tasks)
        
        for result in initial_results:
            await save_council_member_data(job_id, result["name"], {
                "initial_response": result["initial_response"],
                "notes_received": [],
                "final_note": "",
            })
        
        errors = [r for r in initial_results if r.get("error")]
        if len(errors) == len(initial_results):
            raise Exception("All council members failed to respond")

        await update_council_stream_job_status(job_id, "processing", phase="notes")

        # Phase 2: Cross-notes (parallel)
        note_tasks = []
        for member in council_members:
            for other in initial_results:
                if other["name"] != member["name"] and other["initial_response"]:
                    note_tasks.append(_get_cross_note(
                        member,
                        other["name"],
                        other["initial_response"],
                    ))
        
        note_results = await asyncio.gather(*note_tasks)
        
        notes_by_recipient: Dict[str, List[Dict[str, str]]] = {}
        for note in note_results:
            if note.get("note"):
                recipient = note["to_member"]
                if recipient not in notes_by_recipient:
                    notes_by_recipient[recipient] = []
                notes_by_recipient[recipient].append({
                    "from_member": note["from_member"],
                    "note": note["note"],
                })
        
        for member_name, notes in notes_by_recipient.items():
            await update_council_member_data(job_id, member_name, notes_received=notes)

        await update_council_stream_job_status(job_id, "processing", phase="final_notes")

        # Phase 3: Final notes (parallel)
        final_tasks = []
        for result in initial_results:
            if not result.get("error"):
                member = next((m for m in council_members if m["name"] == result["name"]), None)
                if member:
                    notes = notes_by_recipient.get(result["name"], [])
                    final_tasks.append(_get_final_note(
                        member,
                        result["initial_response"],
                        notes,
                    ))
        
        final_results = await asyncio.gather(*final_tasks)
        
        for result in final_results:
            if result.get("final_note"):
                await update_council_member_data(
                    job_id, result["name"],
                    final_note=result["final_note"]
                )

        await update_council_stream_job_status(job_id, "processing", phase="synthesis")

        # Phase 4: Advisor synthesis
        member_data = []
        for member in council_members:
            data = await get_council_member_data(job_id, member["name"])
            if data:
                member_data.append({
                    "name": member["name"],
                    "initial_response": data.get("initial_response", ""),
                    "notes_received": data.get("notes_received", []),
                    "final_note": data.get("final_note", ""),
                })
        
        advisor_response = await _synthesize_advisor_response(
            member_data, openai_messages, user_context
        )
        
        member_responses_json = json.dumps(member_data)
        
        await update_council_stream_job_status(
            job_id, "processing",
            phase="tts",
            advisor_response=advisor_response,
            member_responses=member_responses_json,
        )

        # TTS streaming
        tts_text = await call_openai(
            TTS_CONVERSION_PREPROMPT, advisor_response, temperature=0.1
        )

        tts_sentences = split_into_sentences(tts_text)

        if len(tts_sentences) != len(sentences):
            for i in range(len(sentences)):
                sentence_key = f"council_stream_job:{job_id}:sentence:{i}"
                await redis.delete(sentence_key)

            await redis.hset(f"council_stream_job:{job_id}", "sentence_count", str(len(tts_sentences)))

            for i, sentence in enumerate(tts_sentences):
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

        async def process_sentence(index: int, text: str):
            try:
                tts_job_id = await submit_tts_job_via_agent(text, voice)
                await update_council_stream_sentence(job_id, index, status="processing", tts_job_id=tts_job_id)

                result = await poll_agent_job_result(tts_job_id, "tts")
                audio_base64 = result.get("audio", "")

                await update_council_stream_sentence(job_id, index, status="completed", audio=audio_base64)
                return audio_base64
            except Exception as e:
                await update_council_stream_sentence(job_id, index, status="failed")
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

        await update_council_stream_job_status(
            job_id,
            "completed",
            combined_audio=combined_audio_b64,
        )

    except Exception as e:
        await update_council_stream_job_status(job_id, "failed", error=str(e))
