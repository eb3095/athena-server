"""Audio utilities - sentence splitting, WAV operations."""

import io
import re
import wave
from typing import List


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
