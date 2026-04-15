# AI Scrum Automation System

Real-time multimodal AI system that analyzes live meetings — audio and video — to automatically extract Scrum updates, track action items, and sync them to Trello.

---

## Architecture

```
                        ┌─────────────────────────────┐
                        │        FastAPI Server        │
                        │   (main.py — port 8000)      │
                        └──────────────┬──────────────┘
                                       │
          ┌────────────────────────────┼────────────────────────────┐
          │                            │                            │
   ┌──────▼──────┐             ┌───────▼──────┐            ┌───────▼──────┐
   │ audio_service│             │vision_service│            │trello_service│
   │  Pyannote    │             │  Qwen-VL     │            │  Trello API  │
   │  (diarize)   │             │  (frames)    │            │  (cards)     │
   └──────┬──────┘             └───────┬──────┘            └──────────────┘
          │                            │
          └────────────┬───────────────┘
                       │
               ┌───────▼──────┐
               │fusion_service│
               │ (time-align  │
               │  + events)   │
               └───────┬──────┘
                       │
               ┌───────▼──────┐
               │reasoning_svc │
               │  Qwen 2.5    │  ← live Scrum updates
               │  DeepSeek-R1 │  ← post-meeting report
               └───────┬──────┘
                       │
          ┌────────────┴────────────┐
          │                         │
   ┌──────▼──────┐          ┌───────▼──────┐
   │    Redis     │          │  websocket   │
   │ (state store)│          │   service    │
   └─────────────┘          │ (live UI)    │
                             └─────────────┘
```

---

## Prerequisites

- Python 3.10+
- Redis 6+ (local or Docker)
- CUDA-capable GPU (optional — CPU fallback available, but significantly slower)
- [HuggingFace](https://huggingface.co/) account with access to `pyannote/speaker-diarization-3.1`
- Trello account with API key, token, and a target board ID

---

## Installation

**1. Clone and install dependencies:**

```bash
git clone <repo-url>
cd ai-scrum-automation
pip install -r requirements.txt
```

**2. Start Redis:**

```bash
# Docker (recommended)
docker run -d -p 6379:6379 redis

# Or use a local Redis installation
redis-server
```

**3. Create your `.env` file:**

```bash
cp .env.example .env   # if available, otherwise create manually
```

Minimum required fields:

```env
PYANNOTE_TOKEN=hf_your_huggingface_token

TRELLO_API_KEY=your_trello_api_key
TRELLO_TOKEN=your_trello_token
TRELLO_BOARD_ID=your_trello_board_id
```

All other fields have sensible defaults (see Configuration below).

---

## Running the Server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`

### Docker

```bash
docker build -t ai-scrum-system .
docker run -p 8000:8000 --env-file .env ai-scrum-system
```

---

## Configuration

All settings are loaded from `.env` via `configs/settings.py`. Every field has a default and can be overridden.

### Redis

| Variable | Default | Description |
|---|---|---|
| `REDIS_HOST` | `localhost` | Redis server hostname |
| `REDIS_PORT` | `6379` | Redis server port |
| `REDIS_DB` | `0` | Redis database index |
| `REDIS_PASSWORD` | _(none)_ | Redis password (optional) |

### Redis Stream Keys

| Variable | Default | Description |
|---|---|---|
| `AUDIO_EVENT_STREAM` | `audio:events` | Stream key for audio events |
| `VIDEO_EVENT_STREAM` | `video:events` | Stream key for video events |
| `FUSION_EVENT_STREAM` | `fusion:events` | Stream key for fusion events |
| `LIVE_UPDATES_STREAM` | `live:updates` | Stream key for live Scrum updates |

### Audio Service

| Variable | Default | Description |
|---|---|---|
| `PYANNOTE_MODEL` | `pyannote/speaker-diarization-3.1` | HuggingFace model for speaker diarization |
| `PYANNOTE_DEVICE` | `cuda` | Device for diarization (`cuda` or `cpu`) |
| `PYANNOTE_TOKEN` | _(none)_ | HuggingFace token — **required** for pyannote |
| `ASR_MODEL` | `Parakeet-ASR` | Speech-to-text model name |
| `ASR_DEVICE` | `cuda` | Device for ASR (`cuda` or `cpu`) |
| `CHUNK_DURATION` | `3.0` | Audio chunk size in seconds |

### Vision Service

| Variable | Default | Description |
|---|---|---|
| `QWEN_VL_MODEL` | `Qwen/Qwen2-VL-2B-Instruct` | Vision-language model for frame analysis |
| `QWEN_VL_DEVICE` | `cuda` | Device for vision model |
| `VIDEO_SAMPLE_INTERVAL` | `1.0` | Seconds between sampled video frames |

### Reasoning Service

| Variable | Default | Description |
|---|---|---|
| `QWEN_MODEL` | `Qwen/Qwen2.5-7B-Instruct` | Live reasoning model (real-time Scrum updates) |
| `QWEN_DEVICE` | `cuda` | Device for Qwen |
| `DEEPSEEK_MODEL` | `deepseek-ai/DeepSeek-R1` | Post-meeting refinement model |
| `DEEPSEEK_DEVICE` | `cuda` | Device for DeepSeek |

### Trello Integration

| Variable | Default | Description |
|---|---|---|
| `TRELLO_API_KEY` | _(none)_ | Trello REST API key |
| `TRELLO_TOKEN` | _(none)_ | Trello access token |
| `TRELLO_BOARD_ID` | _(none)_ | Target Trello board ID |

### API & WebSocket

| Variable | Default | Description |
|---|---|---|
| `API_HOST` | `0.0.0.0` | FastAPI bind host |
| `API_PORT` | `8000` | FastAPI bind port |
| `WS_HOST` | `0.0.0.0` | WebSocket server host |
| `WS_PORT` | `8765` | WebSocket server port |

---

## API Reference

### Meeting Management

#### `POST /meeting/start`

Start a new meeting session. Returns a `session_id` used for all subsequent calls.

```json
// Request body (optional)
{ "session_name": "Sprint 42 Planning" }

// Response
{ "session_id": "uuid-string", "status": "active" }
```

#### `POST /meeting/end`

End a session and optionally generate a final Scrum report via DeepSeek.

```json
// Request body
{ "session_id": "uuid-string", "generate_report": true }

// Response
{ "session_id": "uuid-string", "status": "ended", "report": { ... } }
```

#### `GET /meeting/{session_id}`

Retrieve session state, event counts, and the final report (if generated).

```json
// Response
{
  "session_id": "uuid-string",
  "session_info": { "started_at": "...", "status": "ended" },
  "fusion_events_count": 12,
  "scrum_updates_count": 5,
  "report": { ... }
}
```

### Media Processing

#### `POST /audio/process?session_id={session_id}`

Upload a WAV audio chunk for processing. Accepts mono or stereo WAV; stereo is automatically averaged to mono.

```bash
curl -X POST "http://localhost:8000/audio/process?session_id=<id>" \
  -F "file=@chunk.wav"
```

```json
// Response
{ "session_id": "...", "fusion_events_count": 1 }
```

#### `POST /video/process?session_id={session_id}`

Upload a video frame (JPEG/PNG) for visual context analysis.

```bash
curl -X POST "http://localhost:8000/video/process?session_id=<id>" \
  -F "file=@frame.jpg"
```

```json
// Response
{ "session_id": "...", "visual_context": "Whiteboard showing sprint backlog..." }
```

### Trello Integration

#### `POST /trello/sync`

Create Trello cards from all Scrum updates captured in a session.

```json
// Request body
{ "session_id": "uuid-string", "list_id": "trello-list-id" }

// Response
{ "session_id": "...", "cards_created": 3, "results": [ ... ] }
```

#### `GET /trello/lists`

Fetch all lists from the configured Trello board.

```json
// Response
{ "lists": [ { "id": "...", "name": "To Do" }, ... ] }
```

### WebSocket

#### `WS /ws/{session_id}`

Connect to receive real-time Scrum updates as they are extracted during the meeting.

---

## WebSocket Usage Example

```javascript
const sessionId = "your-session-id";
const ws = new WebSocket(`ws://localhost:8000/ws/${sessionId}`);

ws.onopen = () => {
  console.log("Connected to live meeting stream");
};

ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  // update shape: { task, assignee, status, priority, ... }
  console.log("Scrum update:", update);
};

ws.onclose = () => {
  console.log("Session stream closed");
};

// Send a ping or custom message
ws.send(JSON.stringify({ type: "ping" }));
```

Python example using `websockets`:

```python
import asyncio
import websockets
import json

async def listen(session_id: str):
    uri = f"ws://localhost:8000/ws/{session_id}"
    async with websockets.connect(uri) as ws:
        async for message in ws:
            update = json.loads(message)
            print("Live update:", update)

asyncio.run(listen("your-session-id"))
```

---

## Graceful Degradation

All AI models are loaded lazily at startup. If a model fails to load (missing token, no GPU, network error), the affected service returns empty/default results rather than crashing the server.

| Failure | Behavior |
|---|---|
| Pyannote token missing or invalid | `diarize()` returns `[]` — no speaker segments |
| ASR model unavailable | `transcribe()` returns `""` — empty transcript |
| Qwen-VL fails to load | `analyze_frame()` returns event with empty `visual_context` |
| Qwen 2.5 fails to load | `analyze_meeting_event()` returns `None` — no Scrum update emitted |
| DeepSeek fails to load | `refine_session()` returns empty `ScrumReport` |
| Trello credentials missing | `create_card()` returns `None` — sync skipped silently |
| Redis unavailable | Startup logs error; session state operations fail gracefully |

The FastAPI server remains up and all endpoints remain reachable regardless of model load failures.

---

## Known Limitations

- **No persistent storage** — all session state lives in Redis. Restarting Redis loses all session data.
- **Single-node only** — no horizontal scaling; Redis streams are not partitioned across workers.
- **WAV only** — `/audio/process` accepts WAV format only; MP3/OGG require pre-conversion.
- **GPU memory** — running all models simultaneously (Pyannote + Qwen-VL + Qwen 2.5 + DeepSeek-R1) requires ~24 GB VRAM. On CPU, inference is significantly slower.
- **No authentication** — the API has no auth layer. Do not expose port 8000 publicly without a reverse proxy and auth middleware.
- **Trello rate limits** — bulk syncing large sessions may hit Trello's API rate limits (300 req/10s per token).

## Future Work

- Add JWT/API-key authentication to all endpoints
- Support streaming audio input via WebSocket (eliminate chunked upload latency)
- Persist session data to PostgreSQL for long-term storage and analytics
- Add a React/Next.js dashboard for live meeting visualization
- Support additional task board integrations (Jira, Linear, GitHub Projects)
- Containerize with Docker Compose (Redis + API in one command)
- Add Prometheus metrics and structured logging
