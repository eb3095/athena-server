<p align="center">
  <img src="logo.png" alt="Athena Logo" width="200">
</p>

# athena-server

LLM prompting API that wraps OpenAI for text generation with text-to-speech via distributed TTS agents.

## Features

- OpenAI-powered text generation with configurable model and parameters
- **Council Mode** - Multi-advisor AI consultation with parallel processing and synthesis
- Distributed TTS via agent system (athena-tts agents register and process jobs)
- **Streaming TTS** - Sentence-by-sentence audio generation for faster perceived response
- Async job queue with polling for long-running requests
- Agent heartbeat monitoring with automatic dead agent detection
- **Central voice management** - Server stores and distributes voice files to TTS agents
- Dual-prompt strategy: markdown-formatted response for display, plain text for TTS
- Configurable system prompts for formatting and personality
- Job timeout and automatic cleanup of stale/completed jobs
- Rate limiting and IP banning for security
- Bearer token authentication
- Redis-backed job and agent state

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Android Client │────▶│  athena-server  │◀────│   athena-tts    │
│                 │     │                 │     │    (agent)      │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                 │
                        ┌────────▼────────┐
                        │      Redis      │
                        │  (jobs, agents) │
                        └─────────────────┘
```

1. Client submits prompt job to server
2. Server calls OpenAI for text generation
3. If TTS requested, server creates TTS job in Redis queue
4. TTS agent polls for jobs, processes them, reports completion
5. Server returns completed response to client

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `AUTH_TOKEN` | Bearer token for API authentication | (required) |
| `OPENAI_API_KEY` | OpenAI API key | (required) |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379` |
| `OPENAI_MODEL` | OpenAI model to use | `gpt-4o` |
| `OPENAI_TEMPERATURE` | Sampling temperature | `0.7` |
| `OPENAI_MAX_TOKENS` | Maximum tokens in response | `4096` |
| `DEFAULT_VOICE` | Default speaker voice name | |
| `VOICES_DIR` | Directory for voice file storage | `/voices` |
| `AGENT_KEY` | Shared secret for agent authentication | (required for agents) |
| `AGENT_JOB_TTL_SECONDS` | How long agent jobs stay in queue | `300` |
| `AGENT_MISSED_HEARTBEATS` | Heartbeats missed before agent is "dead" | `3` |
| `AGENT_RETENTION_DAYS` | Days to retain agent registration | `30` |
| `AGENT_JOB_TIMEOUT_MINUTES` | Minutes before pending/assigned jobs timeout | `30` |
| `COMPLETED_JOB_RETENTION_HOURS` | Hours to retain completed jobs | `6` |
| `STREAM_SENTENCE_PAUSE_MS` | Pause between sentences in combined streaming audio | `500` |
| `MAX_CONVERSATION_MESSAGES` | Maximum messages in conversation context | `20` |
| `FORMATTING_PREPROMPT` | System prompt for markdown formatting | See below |
| `PERSONALITIES` | JSON array of personality objects `[{key, personality}]` | See below |
| `TTS_CONVERSION_PREPROMPT` | System prompt for converting display response to spoken form | See below |
| `RATE_LIMIT_REQUESTS` | Max requests per window | `300` |
| `RATE_LIMIT_WINDOW_SECONDS` | Rate limit window duration | `60` |
| `AUTH_FAIL_BAN_THRESHOLD` | Auth failures before IP ban | `3` |
| `AUTH_FAIL_BAN_DURATION_SECONDS` | Ban duration | `604800` (1 week) |
| `COUNCIL_MEMBERS` | JSON array of council member configs `[{name, prompt}]` | See below |
| `COUNCIL_ADVISOR_PROMPT` | System prompt for the advisor that synthesizes responses | See below |

### Default Preprompts

**Formatting Preprompt:**
```
Keep responses conversational and natural. Only use markdown formatting (headers, bullet points, code blocks) when the content genuinely benefits from structure - like lists of items, code examples, or complex multi-part explanations. For simple questions and casual conversation, respond in plain text without any formatting.
```

**Default Personalities:**
```json
[
  {"key": "default", "personality": "You are a helpful AI assistant."},
  {"key": "flirty", "personality": "You are a playful and flirtatious AI assistant..."},
  {"key": "nerdy", "personality": "You are an enthusiastic nerdy AI assistant..."},
  {"key": "quirky", "personality": "You are a quirky and eccentric AI assistant..."},
  {"key": "professional", "personality": "You are a formal and professional AI assistant..."},
  {"key": "pirate", "personality": "You are a pirate AI assistant..."}
]
```

Clients can query available personalities via `GET /api/personalities` and select one by key when making requests.

**TTS Conversion Preprompt:**
```
Convert this text to spoken form for a user who is speaking, not typing. Keep it as close to the original wording as possible. Only make minimal changes: remove markdown symbols, convert lists to sentences, spell out abbreviations. Do not rephrase, summarize, or add anything. Never say words like 'bullet', 'asterisk', or 'code block'. Output only the converted text.
```

## API

### GET /api/personalities

List available personalities that clients can use.

**Response:**
```json
{
  "personalities": [
    {"key": "default", "personality": "You are a helpful AI assistant."},
    {"key": "pirate", "personality": "You are a pirate AI assistant..."}
  ]
}
```

### POST /api/prompt/job

Submit an async prompt job. Returns immediately with job ID for polling.

**Request:**
```json
{
  "prompt": "What is the capital of France?",
  "speaker": true,
  "speaker_voice": "voice-name",
  "personality": "pirate",
  "personality_custom": null
}
```

Use `personality` to select a server-defined personality by key, or `personality_custom` to provide a custom personality prompt. If neither is specified, uses "default".

**Response (202 Accepted):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending"
}
```

### GET /api/prompt/job/{job_id}

Get the status of a prompt job.

**Response (processing):**
```json
{
  "job_id": "...",
  "status": "processing",
  "response": null,
  "audio": null,
  "error": null
}
```

**Response (completed):**
```json
{
  "job_id": "...",
  "status": "completed",
  "response": "The capital of France is **Paris**...",
  "audio": "base64-encoded-wav-data",
  "error": null
}
```

### POST /api/speak/job

Submit an async TTS-only job (no LLM, just text-to-speech).

**Request:**
```json
{
  "text": "Hello, this is a test.",
  "speaker_voice": "voice-name"
}
```

### GET /api/speak/job/{job_id}

Get the status of a speak job.

### POST /api/stream/job

Submit an async streaming prompt job. Breaks response into sentences and processes TTS in parallel for faster perceived response.

**Request:**
```json
{
  "prompt": "Tell me about the weather",
  "speaker_voice": "voice-name"
}
```

**Response (202 Accepted):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending"
}
```

### GET /api/stream/job/{job_id}

Get the status of a streaming job. Returns individual sentence audio as they complete.

**Response (processing):**
```json
{
  "job_id": "...",
  "status": "processing",
  "response": "The weather is nice today. It's sunny and warm.",
  "sentences": [
    {"index": 0, "text": "The weather is nice today.", "status": "completed", "audio": "base64..."},
    {"index": 1, "text": "It's sunny and warm.", "status": "processing", "audio": null}
  ],
  "combined_audio": null,
  "error": null
}
```

**Response (completed):**
```json
{
  "job_id": "...",
  "status": "completed",
  "response": "The weather is nice today. It's sunny and warm.",
  "sentences": [
    {"index": 0, "text": "The weather is nice today.", "status": "completed", "audio": "base64..."},
    {"index": 1, "text": "It's sunny and warm.", "status": "completed", "audio": "base64..."}
  ],
  "combined_audio": "base64-all-sentences-with-pauses",
  "error": null
}
```

### POST /api/conversation/job

Submit an async conversation job with message history. Supports multi-turn conversations with context.

**Request:**
```json
{
  "messages": [
    {"role": "user", "content": "What's the capital of France?"},
    {"role": "assistant", "content": "The capital of France is Paris."},
    {"role": "user", "content": "What's its population?"}
  ],
  "speaker": true,
  "speaker_voice": "voice-name"
}
```

**Response (202 Accepted):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending"
}
```

### GET /api/conversation/job/{job_id}

Get the status of a conversation job.

### POST /api/conversation/stream/job

Submit a streaming conversation job with message history. Same as conversation/job but with sentence-by-sentence audio.

### GET /api/conversation/stream/job/{job_id}

Get the status of a streaming conversation job.

### GET /api/council/members

List available council members.

**Response:**
```json
{
  "members": [
    {"name": "The Strategist", "prompt": "You are a strategic thinker..."},
    {"name": "The Skeptic", "prompt": "You question assumptions..."}
  ]
}
```

### POST /api/council/job

Submit an async council job. Multiple AI advisors discuss the question, then an Advisor synthesizes their perspectives.

**Request:**
```json
{
  "messages": [
    {"role": "user", "content": "Should I change careers?"}
  ],
  "speaker_voice": "voice-name",
  "council_members": ["The Strategist", "The Skeptic"],
  "custom_members": [{"name": "Custom", "prompt": "You are..."}],
  "user_traits": ["ambitious", "risk-averse"],
  "user_goal": "Find fulfilling work"
}
```

**Response (completed):**
```json
{
  "job_id": "...",
  "status": "completed",
  "advisor_response": "Based on the council's discussion...",
  "member_responses": [
    {
      "name": "The Strategist",
      "initial_response": "...",
      "notes": [{"from": "The Skeptic", "note": "..."}],
      "final_note": "..."
    }
  ],
  "audio": "base64...",
  "error": null
}
```

### GET /api/council/job/{job_id}

Get the status of a council job.

### POST /api/council/stream/job

Submit a streaming council job with sentence-by-sentence audio.

### GET /api/council/stream/job/{job_id}

Get the status of a streaming council job.

### POST /api/format/text

Clean up speech-to-text output using AI (adds punctuation, fixes grammar).

**Request:**
```json
{
  "text": "hello how are you doing today"
}
```

**Response:**
```json
{
  "formatted_text": "Hello, how are you doing today?"
}
```

### POST /api/summarize

Generate a short summary/title from text.

**Request:**
```json
{
  "text": "What's the weather like in Paris today?",
  "max_words": 6
}
```

**Response:**
```json
{
  "summary": "Weather in Paris inquiry"
}
```

### GET /api/voices

List available voice names from server storage.

**Response:**
```json
{
  "voices": ["freeman", "gellar", "voice3"]
}
```

### GET /api/voices/list

List available voices with metadata (name, checksum, size).

**Response:**
```json
{
  "voices": [
    {"name": "freeman", "checksum": "abc123...", "size": 1234567},
    {"name": "gellar", "checksum": "def456...", "size": 2345678}
  ]
}
```

### GET /api/voices/{name}/download

Download a voice file. Requires agent key authentication (X-Agent-Key header).

**Response:** WAV file with `X-Voice-Checksum` header.

### POST /api/voices/{name}/upload

Upload a voice file. Requires agent key authentication (X-Agent-Key header).

**Request body:** Raw WAV file content (max 50MB).

**Response:**
```json
{
  "status": "success",
  "voice": {"name": "newvoice", "checksum": "ghi789...", "size": 3456789}
}
```

### DELETE /api/voices/{name}

Delete a voice file. Requires agent key authentication (X-Agent-Key header).

**Response:**
```json
{
  "status": "success",
  "message": "Voice 'voicename' deleted"
}
```

### GET /api/agents

List registered agents and their status.

**Response:**
```json
{
  "agents": [
    {
      "agent_id": "...",
      "service_type": "tts",
      "registered_at": 1234567890.0,
      "last_seen": 1234567950.0,
      "status": "active",
      "speakers": ["voice1", "voice2"]
    }
  ]
}
```

### GET /health

Health check endpoint.

## Agent API (for TTS agents)

These endpoints are used by athena-tts agents:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents/register` | POST | Register agent with server |
| `/api/agents/heartbeat` | POST | Send heartbeat |
| `/api/agents/jobs/poll` | POST | Poll for available jobs |
| `/api/agents/jobs/{id}/complete` | POST | Report job completion |
| `/api/voices/list` | GET | Get voice list with checksums (for sync) |
| `/api/voices/{name}/download` | GET | Download voice file |

## Usage

### Docker

```bash
# Build
make build

# Run (requires OPENAI_API_KEY and Redis)
OPENAI_API_KEY=sk-... make run

# View logs
make logs

# Stop
make stop
```

### Local Development

```bash
# Install dependencies
make install

# Run server (requires Redis)
OPENAI_API_KEY=sk-... AUTH_TOKEN=test-token REDIS_URL=redis://localhost:6379 ./venv/bin/python server.py

# Format code
make fmt

# Lint
make lint
```

### Example Requests

```bash
# Submit async prompt job
curl -X POST http://localhost:5003/api/prompt/job \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me a joke", "speaker": true}'

# Poll for result
curl http://localhost:5003/api/prompt/job/{job_id} \
  -H "Authorization: Bearer test-token"

# List voices
curl http://localhost:5003/api/voices \
  -H "Authorization: Bearer test-token"

# List agents
curl http://localhost:5003/api/agents \
  -H "Authorization: Bearer test-token"
```

## Helm Deployment

```bash
# Install (requires Redis)
helm install athena-server ./helm/athena-server \
  --set auth.token=your-token \
  --set openai.apiKey=sk-... \
  --set agent.key=shared-agent-secret \
  --set redis.url=redis://redis:6379

# Upgrade
helm upgrade athena-server ./helm/athena-server

# Uninstall
helm uninstall athena-server
```

### Helm Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `auth.token` | API authentication token | (required) |
| `openai.apiKey` | OpenAI API key | (required) |
| `agent.key` | Shared secret for agents | (required) |
| `agent.jobTtlSeconds` | Agent job TTL | `300` |
| `agent.missedHeartbeats` | Heartbeats before dead | `3` |
| `agent.retentionDays` | Agent retention days | `30` |
| `agent.jobTimeoutMinutes` | Job timeout | `30` |
| `agent.completedJobRetentionHours` | Completed job retention | `6` |
| `persistence.voices.enabled` | Enable voice storage PVC | `false` |
| `persistence.voices.size` | Voice storage size | `1Gi` |
| `persistence.voices.storageClass` | Storage class for PVC | `""` |
| `redis.enabled` | Deploy Redis with chart | `true` |
| `redis.url` | Redis URL | `redis://athena-redis:6379` |

## Requirements

- Python 3.11+
- Redis
- athena-tts agent(s) for TTS functionality
- OpenAI API key
