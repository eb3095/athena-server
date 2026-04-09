<p align="center">
  <img src="logo.png" alt="Athena Logo" width="200">
</p>

# athena-server

LLM prompting API that wraps OpenAI for text generation with text-to-speech via distributed TTS agents.

## Features

- OpenAI-powered text generation with configurable model and parameters
- Distributed TTS via agent system (athena-tts agents register and process jobs)
- Async job queue with polling for long-running requests
- Agent heartbeat monitoring with automatic dead agent detection
- Aggregated voice list from all active TTS agents
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
| `AGENT_KEY` | Shared secret for agent authentication | (required for agents) |
| `AGENT_JOB_TTL_SECONDS` | How long agent jobs stay in queue | `300` |
| `AGENT_MISSED_HEARTBEATS` | Heartbeats missed before agent is "dead" | `3` |
| `AGENT_RETENTION_DAYS` | Days to retain agent registration | `30` |
| `AGENT_JOB_TIMEOUT_MINUTES` | Minutes before pending/assigned jobs timeout | `30` |
| `COMPLETED_JOB_RETENTION_HOURS` | Hours to retain completed jobs | `6` |
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
Convert this text to spoken form for a user who is speaking, not typing. Keep it as close to the original wording as possible. Only make minimal changes: remove markdown symbols, convert lists to sentences, spell out abbreviations. Do not rephrase, summarize, or add anything. Never say words like 'bullet', 'asterisk', or 'code block'. Output only the converted text.
```

## API

### POST /api/prompt/job

Submit an async prompt job. Returns immediately with job ID for polling.

**Request:**
```json
{
  "prompt": "What is the capital of France?",
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

### GET /api/voices

List available voices from active TTS agents.

**Response:**
```json
{
  "voices": ["voice1", "voice2", "voice3"]
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
| `/api/agents/heartbeat` | POST | Send heartbeat (includes speaker list) |
| `/api/agents/jobs/poll` | POST | Poll for available jobs |
| `/api/agents/jobs/{id}/complete` | POST | Report job completion |

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
| `redis.enabled` | Deploy Redis with chart | `true` |
| `redis.url` | Redis URL | `redis://athena-redis:6379` |

## Requirements

- Python 3.11+
- Redis
- athena-tts agent(s) for TTS functionality
- OpenAI API key
