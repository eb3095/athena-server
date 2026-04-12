"""OpenAI client and API wrappers."""

from typing import List, Optional

from openai import AsyncOpenAI

from athena.config import (
    OPENAI_API_KEY,
    OPENAI_MAX_TOKENS,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    OPENAI_TIMEOUT,
)


# Global OpenAI client instance
openai_client: Optional[AsyncOpenAI] = None


def init_openai() -> AsyncOpenAI:
    """Initialize the OpenAI client."""
    global openai_client
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required")
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return openai_client


def get_openai() -> AsyncOpenAI:
    """Get the OpenAI client instance."""
    if openai_client is None:
        raise RuntimeError("OpenAI client not initialized")
    return openai_client


async def call_openai(
    system_prompt: str, user_prompt: str, temperature: Optional[float] = None
) -> str:
    """Call OpenAI with a single user message."""
    client = get_openai()
    response = await client.chat.completions.create(
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


async def call_openai_conversation(
    system_prompt: str, messages: List[dict], temperature: Optional[float] = None
) -> str:
    """Call OpenAI with conversation history."""
    client = get_openai()
    openai_messages = [{"role": "system", "content": system_prompt}]
    openai_messages.extend(messages)

    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=temperature if temperature is not None else OPENAI_TEMPERATURE,
        max_tokens=OPENAI_MAX_TOKENS,
        timeout=OPENAI_TIMEOUT,
        messages=openai_messages,
    )
    return response.choices[0].message.content or ""
