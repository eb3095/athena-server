"""Voice file management service."""

import hashlib
import os
import re
from typing import List

import aiofiles
import aiofiles.os

from athena.config import VOICES_DIR


def get_voice_checksum(filepath: str) -> str:
    """Calculate MD5 checksum of a voice file."""
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_available_voices() -> List[dict]:
    """Get list of available voices from local storage with metadata."""
    voices = []
    try:
        if not os.path.exists(VOICES_DIR):
            os.makedirs(VOICES_DIR, exist_ok=True)
            return voices

        for filename in os.listdir(VOICES_DIR):
            if filename.endswith(".wav"):
                filepath = os.path.join(VOICES_DIR, filename)
                name = filename[:-4]
                try:
                    checksum = get_voice_checksum(filepath)
                    size = os.path.getsize(filepath)
                    voices.append({
                        "name": name,
                        "checksum": checksum,
                        "size": size
                    })
                except OSError:
                    pass
    except OSError:
        pass
    return sorted(voices, key=lambda v: v["name"])


def get_voice_names() -> List[str]:
    """Get list of available voice names."""
    return [v["name"] for v in get_available_voices()]


def get_voice_filepath(name: str) -> str:
    """Get the filepath for a voice file."""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    return os.path.join(VOICES_DIR, f"{safe_name}.wav")


async def save_voice_file(name: str, content: bytes) -> dict:
    """Save a voice file to storage."""
    os.makedirs(VOICES_DIR, exist_ok=True)

    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    if not safe_name:
        raise ValueError("Invalid voice name")

    filepath = os.path.join(VOICES_DIR, f"{safe_name}.wav")

    async with aiofiles.open(filepath, "wb") as f:
        await f.write(content)

    checksum = get_voice_checksum(filepath)
    size = os.path.getsize(filepath)

    return {
        "name": safe_name,
        "checksum": checksum,
        "size": size
    }


async def delete_voice_file(name: str) -> bool:
    """Delete a voice file from storage."""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    if not safe_name:
        return False

    filepath = os.path.join(VOICES_DIR, f"{safe_name}.wav")

    try:
        await aiofiles.os.remove(filepath)
        return True
    except OSError:
        return False
