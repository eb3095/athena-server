# athena-server

LLM prompting API that wraps OpenAI for text generation with optional text-to-speech via athena-tts.

## Features

- OpenAI-powered text generation with configurable model and parameters
- Optional text-to-speech via athena-tts integration
- Dual-prompt strategy: markdown-formatted response for display, plain text for TTS
- Configurable system prompts for formatting and personality
- Rate limiting and IP banning for security
- Bearer token authentication

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `AUTH_TOKEN` | Bearer token for API authentication | (required) |
| `OPENAI_API_KEY` | OpenAI API key | (required) |
| `OPENAI_MODEL` | OpenAI model to use | `gpt-4o` |
| `OPENAI_TEMPERATURE` | Sampling temperature | `0.7` |
| `OPENAI_MAX_TOKENS` | Maximum tokens in response | `4096` |
| `ATHENA_TTS_URL` | URL of athena-tts service | `http://localhost:5002` |
| `ATHENA_TTS_TOKEN` | Auth token for athena-tts | |
| `DEFAULT_VOICE` | Default speaker voice name | |
| `FORMATTING_PREPROMPT` | System prompt for markdown formatting | See below |
| `PERSONALITY_PREPROMPT` | System prompt for personality/behavior | See below |
| `TTS_CONVERSION_PREPROMPT` | System prompt for converting display response to spoken form | See below |
| `RATE_LIMIT_REQUESTS` | Max requests per window | `300` |
| `RATE_LIMIT_WINDOW_SECONDS` | Rate limit window duration | `60` |
| `AUTH_FAIL_BAN_THRESHOLD` | Auth failures before IP ban | `3` |
| `AUTH_FAIL_BAN_DURATION_SECONDS` | Ban duration | `604800` (1 week) |

### Default Preprompts

**Formatting Preprompt:**
```
Format your response using markdown when appropriate. Use headers, bullet points, code blocks, and emphasis to make the response clear and readable.
```

**Personality Preprompt:**
```
You are a helpful AI assistant.
```

**TTS Conversion Preprompt:**
```
Convert this text to spoken form while keeping it as close to the original wording as possible. Only make minimal changes: remove markdown symbols, convert lists to sentences, spell out abbreviations. Do not rephrase, summarize, or add anything. Never say words like 'bullet', 'asterisk', or 'code block'. Output only the converted text.
```

## API

### POST /api/prompt

Generate a response to a prompt, optionally with text-to-speech.

**Request:**
```json
{
  "prompt": "What is the capital of France?",
  "speaker": false,
  "speaker_voice": "voice-name"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | yes | The user's prompt |
| `speaker` | boolean | no | Generate audio (default: false) |
| `speaker_voice` | string | no | Voice to use (defaults to DEFAULT_VOICE) |

**Response:**
```json
{
  "response": "The capital of France is **Paris**...",
  "audio": "base64-encoded-wav-data"
}
```

The `audio` field is only present when `speaker=true`.

### GET /api/voices

List available voices from athena-tts.

**Response:**
```json
{
  "voices": ["voice1", "voice2", "voice3"]
}
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy"
}
```

## Usage

### Docker

```bash
# Build
make build

# Run (requires OPENAI_API_KEY)
OPENAI_API_KEY=sk-... make run

# View logs
make logs

# Test
make test

# Stop
make stop
```

### Local Development

```bash
# Install dependencies
make install

# Run server
OPENAI_API_KEY=sk-... AUTH_TOKEN=test-token ./venv/bin/python server.py

# Format code
make fmt
```

### Example Requests

```bash
# Simple prompt
curl -X POST http://localhost:5003/api/prompt \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain quantum computing in simple terms"}'

# Prompt with speech
curl -X POST http://localhost:5003/api/prompt \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me a joke", "speaker": true, "speaker_voice": "myvoice"}' \
  | jq -r '.audio' | base64 -d > output.wav

# List voices
curl -X GET http://localhost:5003/api/voices \
  -H "Authorization: Bearer test-token"
```

## Helm Deployment

```bash
# Install
helm install athena-server ./helm/athena-server \
  --set auth.token=your-token \
  --set openai.apiKey=sk-... \
  --set athenaTts.url=http://athena-tts:5002 \
  --set athenaTts.token=tts-token

# Upgrade
helm upgrade athena-server ./helm/athena-server

# Uninstall
helm uninstall athena-server
```

## Architecture

When `speaker=true`, athena-server makes two OpenAI calls:

1. **Display response**: Uses formatting + personality preprompts to generate markdown-formatted text
2. **TTS conversion**: Converts the display response into natural spoken form (strips markdown, spells out abbreviations, etc.)

The display response is returned to the user while the converted TTS text is sent to athena-tts for audio synthesis. This ensures the spoken audio matches the displayed text content exactly.

## Requirements

- Python 3.11+
- athena-tts service (for TTS functionality)
- OpenAI API key
