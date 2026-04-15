# Bugfix Requirements Document

## Introduction

This document captures the full-scale audit findings for the **AI Scrum Automation System** — a real-time multimodal FastAPI application that processes meeting audio/video, extracts Scrum updates via LLMs, and syncs results to Trello via Redis streams and WebSockets.

A systematic review of every file, module, import, and service connection revealed multiple categories of bugs: broken/missing imports, startup crash risks, incorrect API usage, logic errors, invalid type usage, missing dependencies in `requirements.txt`, and architecture issues. These bugs collectively prevent the application from starting or running correctly in any environment.

---

## Bug Analysis

### Current Behavior (Defect)

**Import & Module Errors**

1.1 WHEN `main.py` is imported at startup THEN the system crashes because `from models import MeetingSession, ScrumUpdate, LiveUpdate` imports `MeetingSession` which is never used in `main.py`, creating dead import noise and masking real import errors.

1.2 WHEN `services/websocket_service/__init__.py` is loaded THEN the system crashes because `import websockets` is used to type `websockets.Server` but the `websockets` library's async server API changed in v10+ and `websockets.serve()` no longer returns a `websockets.Server` object — it returns a `websockets.WebSocketServer`, making the type annotation and `.close()` / `.wait_closed()` call pattern incorrect.

1.3 WHEN `services/websocket_service/__init__.py` is loaded THEN the system has a dual-manager bug: both `websocket_server = WebSocketServer()` and `connection_manager = ConnectionManager()` are instantiated as module-level singletons, but `main.py` imports and uses only `connection_manager` while `WebSocketServer` (which wraps its own internal `ConnectionManager`) is never started — meaning the standalone `websockets` server is never launched and the two `ConnectionManager` instances are completely disconnected.

1.4 WHEN `services/websocket_service/__init__.py` handles a raw `websockets` connection THEN the system crashes because `handle_websocket` calls `websocket.receive_json()` on a raw `websockets.WebSocketServerProtocol` object, which has no `.receive_json()` method — that method belongs to FastAPI's `WebSocket` class only.

1.5 WHEN `services/websocket_service/__init__.py` handles a raw `websockets` connection THEN the system crashes because `WebSocketDisconnect` is a FastAPI exception that is never raised by the raw `websockets` library, so the disconnect handler is never triggered and connections leak.

1.6 WHEN `services/vision_service/__init__.py` calls `process_video_stream` THEN the system crashes with a `NameError` because `AsyncIterator` is used as a type annotation but `AsyncIterator` is not imported — only `asyncio`, `numpy`, `torch`, `PIL.Image`, and `io` are imported at the top of the file.

1.7 WHEN `services/audio_service/__init__.py` calls `SpeakerDiarizationService.diarize` THEN the system crashes because `torch.from_numpy(audio_array)` is called on a 1-D float32 array but `pyannote.audio` Pipeline expects a 2-D tensor of shape `(channels, samples)` — the missing `.unsqueeze(0)` causes a runtime shape error inside the pipeline.

**Logic & Runtime Bugs**

1.8 WHEN `services/fusion_service/__init__.py` calls `stop_session` THEN the system enters an infinite loop because the method iterates `for audio in remaining_audio` but calls `await self._align_and_fuse(session_id)` instead of `await self._align_and_fuse_single(audio, session_id)` — the loop variable `audio` is never consumed, and `_align_and_fuse` re-reads the full buffer on every iteration.

1.9 WHEN `services/fusion_service/__init__.py` calls `_align_and_fuse` THEN the system produces duplicate fusion events on every call because processed audio/video events are never removed from `_audio_buffer` and `_video_buffer` after being fused — every new event causes all previous events to be re-fused and re-published to Redis.

1.10 WHEN `services/reasoning_service/__init__.py` calls `QwenReasoningService.analyze_meeting_event` THEN the system may produce a `ScrumUpdate` with `task=""` (empty string) because `_parse_scrum_update` does `data.get("task") or ""` — an empty-string task passes the `if update:` check in `main.py` but is semantically invalid and pollutes Redis with empty records.

1.11 WHEN `services/reasoning_service/__init__.py` calls `DeepSeekRefinementService._run_refinement` with `do_sample` not set THEN the system raises a `ValueError` from HuggingFace Transformers because `temperature=0.5` is passed without `do_sample=True`, which is required when temperature != 1.0 in recent Transformers versions.

1.12 WHEN `main.py` calls `process_audio` endpoint and reads a WAV file THEN the system crashes for stereo WAV files because `np.frombuffer(frames, dtype=np.int16)` produces an interleaved stereo array that is not reshaped or averaged to mono before being passed to `audio_service.process_audio_chunk`, causing incorrect duration calculation and diarization failures.

1.13 WHEN `main.py` calls `websocket_endpoint` and the client disconnects THEN the system silently swallows all exceptions including `WebSocketDisconnect` because the bare `except Exception` block calls `connection_manager.disconnect(...)` but never re-raises or logs the specific disconnect reason, making debugging impossible.

**Configuration & Environment Bugs**

1.14 WHEN `configs/settings.py` is loaded with `session_stream: str = "session:{session_id}"` THEN the system uses a literal string `"session:{session_id}"` as a Redis stream key instead of a formatted key, because the field is a plain `str` default — the `{session_id}` placeholder is never substituted anywhere in the codebase.

1.15 WHEN `services/audio_service/__init__.py` initializes `SpeakerDiarizationService` THEN the system uses `hasattr(settings, 'pyannote_token')` as a guard, but `pyannote_token` is always present on the `Settings` object (it's defined with `Optional[str] = None`) — the guard is always `True` and passes `None` to `use_auth_token` when the token is not set in `.env`, which causes Pyannote to fail silently or raise an authentication error.

1.16 WHEN `services/trello_service/__init__.py` calls `create_card` THEN the system sends Trello API credentials (`key` and `token`) inside the JSON body of a POST request instead of as query parameters, which is the format Trello's REST API requires — causing all card creation requests to return 401 Unauthorized.

**Requirements / Dependency Bugs**

1.17 WHEN the project is installed from `requirements.txt` THEN the system fails to install because `torch==2.11.0` does not exist — the latest stable PyTorch version as of early 2025 is `2.2.x`, making this an invalid pinned version that causes `pip install` to fail entirely.

1.18 WHEN the project is installed from `requirements.txt` THEN the system is missing `typing_extensions` as an explicit dependency, which is required by `pydantic-settings==2.1.0` and `transformers==4.36.2` but not pinned, risking version conflicts in constrained environments.

1.19 WHEN the project is installed from `requirements.txt` THEN the `websockets==12.0` package is listed but the code in `websocket_service` uses the `websockets` library's server API in a way that is incompatible with v12's breaking changes to the connection handler signature.

---

### Expected Behavior (Correct)

**Import & Module Errors**

2.1 WHEN `main.py` is imported at startup THEN the system SHALL only import models that are actually used, removing `MeetingSession` from the import line.

2.2 WHEN `services/websocket_service/__init__.py` is loaded THEN the system SHALL use the correct `websockets` v10+ server type annotation (`websockets.WebSocketServer`) and the correct shutdown pattern (`server.close(); await server.wait_closed()`).

2.3 WHEN `services/websocket_service/__init__.py` is loaded THEN the system SHALL expose a single `connection_manager` singleton used by FastAPI's `/ws/{session_id}` endpoint, and SHALL NOT instantiate a separate standalone `WebSocketServer` unless it is explicitly started in `startup()` — eliminating the dual-manager split.

2.4 WHEN `services/websocket_service/__init__.py` handles a raw `websockets` connection THEN the system SHALL use `await websocket.recv()` followed by `json.loads(...)` instead of the non-existent `.receive_json()` method.

2.5 WHEN `services/websocket_service/__init__.py` handles a raw `websockets` connection THEN the system SHALL catch `websockets.exceptions.ConnectionClosed` (not `WebSocketDisconnect`) to properly handle client disconnections.

2.6 WHEN `services/vision_service/__init__.py` uses `AsyncIterator` as a type annotation THEN the system SHALL import it via `from typing import AsyncIterator` at the top of the file.

2.7 WHEN `services/audio_service/__init__.py` calls `SpeakerDiarizationService.diarize` THEN the system SHALL reshape the audio tensor to 2-D before passing it to the pipeline: `torch.from_numpy(audio_array).unsqueeze(0)`.

**Logic & Runtime Bugs**

2.8 WHEN `services/fusion_service/__init__.py` calls `stop_session` THEN the system SHALL flush remaining buffered events correctly by passing each individual audio event to the fusion logic, and SHALL clear the buffers after flushing.

2.9 WHEN `services/fusion_service/__init__.py` calls `_align_and_fuse` THEN the system SHALL clear processed events from `_audio_buffer` and `_video_buffer` after fusion to prevent duplicate event publishing.

2.10 WHEN `services/reasoning_service/__init__.py` parses a Qwen response THEN the system SHALL only create a `ScrumUpdate` when `task` is a non-empty, non-null string, rejecting empty-string tasks before they are stored in Redis.

2.11 WHEN `services/reasoning_service/__init__.py` calls `DeepSeekRefinementService._run_refinement` THEN the system SHALL pass `do_sample=True` alongside `temperature=0.5` to satisfy the Transformers generation API contract.

2.12 WHEN `main.py` calls `process_audio` and reads a WAV file THEN the system SHALL handle stereo audio by averaging channels to mono before passing the array to `audio_service.process_audio_chunk`.

2.13 WHEN `main.py` handles a WebSocket disconnect THEN the system SHALL catch `WebSocketDisconnect` explicitly and log the session and reason, rather than silently swallowing all exceptions.

**Configuration & Environment Bugs**

2.14 WHEN session-scoped Redis keys are needed THEN the system SHALL format the stream key dynamically (e.g., `f"session:{session_id}"`) at the call site rather than relying on the literal `settings.session_stream` field, or the `Settings` class SHALL provide a helper method `session_stream_key(session_id: str) -> str`.

2.15 WHEN `services/audio_service/__init__.py` initializes `SpeakerDiarizationService` THEN the system SHALL pass `settings.pyannote_token` directly (it is already `Optional[str]`) and SHALL check `if settings.pyannote_token` before passing it, raising a clear `RuntimeError` or warning when the token is absent.

2.16 WHEN `services/trello_service/__init__.py` calls `create_card` THEN the system SHALL send `key` and `token` as query parameters (via `params=`) rather than inside the JSON body, conforming to the Trello REST API authentication spec.

**Requirements / Dependency Bugs**

2.17 WHEN the project is installed from `requirements.txt` THEN the system SHALL specify a valid, installable PyTorch version (e.g., `torch==2.2.2`) so that `pip install -r requirements.txt` succeeds.

2.18 WHEN the project is installed from `requirements.txt` THEN the system SHALL include all transitive dependencies that are directly used in code (`typing_extensions`, `soundfile` or `librosa` for audio I/O if needed) as explicit pins.

2.19 WHEN the project is installed from `requirements.txt` THEN the `websockets` version SHALL be compatible with the server API usage pattern in `websocket_service` (v10–v12 compatible handler signature).

---

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a valid meeting session is started via `POST /meeting/start` THEN the system SHALL CONTINUE TO create a UUID session ID, store session info in Redis, and return `{"session_id": ..., "status": "active"}`.

3.2 WHEN a valid audio WAV file (mono, 16kHz) is posted to `POST /audio/process` THEN the system SHALL CONTINUE TO run diarization, transcription, fusion, and Qwen reasoning in sequence and return the fusion event count.

3.3 WHEN a valid image is posted to `POST /video/process` THEN the system SHALL CONTINUE TO run Qwen-VL frame analysis and return the visual context description.

3.4 WHEN `POST /meeting/end` is called with `generate_report: true` THEN the system SHALL CONTINUE TO invoke DeepSeek refinement, store the report in Redis, and return the full report in the response.

3.5 WHEN `GET /meeting/{session_id}` is called THEN the system SHALL CONTINUE TO return session info, fusion event count, scrum update count, and report from Redis.

3.6 WHEN `POST /trello/sync` is called with a valid session and list ID THEN the system SHALL CONTINUE TO read scrum updates from Redis and attempt to create Trello cards for each task.

3.7 WHEN a WebSocket client connects to `/ws/{session_id}` THEN the system SHALL CONTINUE TO accept the connection, register it under the session, and broadcast scrum updates to all clients in that session.

3.8 WHEN Redis is unavailable at startup THEN the system SHALL CONTINUE TO raise a clear connection error rather than silently proceeding with a disconnected client.

3.9 WHEN Pyannote or Qwen models fail to load (e.g., missing token, no GPU) THEN the system SHALL CONTINUE TO degrade gracefully — returning empty results rather than crashing the entire API process.

3.10 WHEN `configs/settings.py` is loaded THEN the system SHALL CONTINUE TO read all configuration from `.env` using `pydantic-settings`, with `extra="ignore"` preventing crashes from unexpected environment variables.
