# Full Project Audit Fix — Bugfix Design

## Overview

The AI Scrum Automation System contains 19 confirmed defects spanning four categories: Import & Module Errors (1.1–1.7), Logic & Runtime Bugs (1.8–1.13), Configuration & Environment Bugs (1.14–1.16), and Requirements/Dependency Bugs (1.17–1.19). These defects collectively prevent the application from installing, starting, or running correctly. The fix strategy is surgical: each defect is addressed at its exact location with the minimal change required, preserving all existing API contracts, data models, and service interfaces.

---

## Glossary

- **Bug_Condition (C)**: The set of inputs or states that trigger one or more of the 19 defects.
- **Property (P)**: The desired correct behavior when the bug condition holds — the application installs, starts, and processes requests without crashing or producing invalid data.
- **Preservation**: All existing API endpoints, Redis data structures, Pydantic models, and service interfaces that must remain unchanged by the fix.
- **`connection_manager`**: The `ConnectionManager` singleton in `services/websocket_service/__init__.py` used by FastAPI's `/ws/{session_id}` endpoint.
- **`WebSocketServer`**: The standalone `websockets`-library server class in `websocket_service` — currently unused and architecturally disconnected from FastAPI.
- **`_align_and_fuse`**: The `FusionEngine` method that reads the full audio/video buffer and publishes fusion events to Redis — currently called in a loop without consuming the loop variable.
- **`isBugCondition`**: Pseudocode predicate that returns `true` when any of the 19 defect conditions are present in the running system.

---

## Bug Details

### Bug Condition

The system is defective when any of the 19 audit findings are present in the codebase. The combined bug condition is:

**Formal Specification:**
```
FUNCTION isBugCondition(system_state)
  INPUT: system_state — the current source files, requirements.txt, and runtime environment
  OUTPUT: boolean

  RETURN (
    -- Import & Module Errors
    "MeetingSession" IN main.py.unused_imports                          -- 1.1
    OR websocket_service uses websockets.Server type annotation          -- 1.2
    OR websocket_service instantiates both WebSocketServer AND           -- 1.3
       ConnectionManager as disconnected singletons
    OR websocket_service calls websocket.receive_json() on raw           -- 1.4
       websockets protocol object
    OR websocket_service catches WebSocketDisconnect from websockets     -- 1.5
       library (wrong exception type)
    OR vision_service uses AsyncIterator without importing it            -- 1.6
    OR audio_service passes 1-D tensor to pyannote pipeline              -- 1.7
       (missing .unsqueeze(0))

    -- Logic & Runtime Bugs
    OR fusion_service.stop_session loops without consuming audio event   -- 1.8
    OR fusion_service._align_and_fuse never clears processed buffers     -- 1.9
    OR reasoning_service._parse_scrum_update returns ScrumUpdate         -- 1.10
       with task=""
    OR reasoning_service._run_refinement calls model.generate with      -- 1.11
       temperature != 1.0 but without do_sample=True
    OR main.py process_audio does not convert stereo WAV to mono         -- 1.12
    OR main.py websocket_endpoint catches all exceptions silently        -- 1.13

    -- Configuration & Environment Bugs
    OR settings.session_stream is a literal string with                  -- 1.14
       unformatted "{session_id}" placeholder
    OR audio_service uses hasattr guard for pyannote_token               -- 1.15
       (always True on Settings object)
    OR trello_service.create_card sends key/token in JSON body           -- 1.16
       instead of query params

    -- Dependency Bugs
    OR requirements.txt pins torch==2.11.0 (non-existent version)        -- 1.17
    OR requirements.txt omits typing_extensions                          -- 1.18
    OR websockets version is incompatible with server API usage          -- 1.19
  )
END FUNCTION
```

### Examples

- **1.7**: `audio_service.diarize(np.zeros(16000, dtype=np.float32), 16000)` → pyannote raises `ValueError: waveform must be 2D` because `torch.from_numpy(array)` produces shape `(16000,)` instead of `(1, 16000)`.
- **1.8**: `fusion_engine.stop_session("s1")` with 3 buffered audio events → infinite loop; `_align_and_fuse` is called 3 times but each call re-reads all 3 events from the buffer, never draining it.
- **1.9**: Two calls to `process_audio_event` → Redis receives 3 fusion events (1 from first call, 2 from second call re-processing the first event again).
- **1.11**: `deepseek_service._run_refinement(prompt)` → `ValueError: You have set temperature=0.5 but do_sample=False` from Transformers ≥4.35.
- **1.16**: `trello_service.create_card(card)` → Trello API returns `401 Unauthorized` because `key`/`token` are in the JSON body, not query params.
- **1.17**: `pip install -r requirements.txt` → `ERROR: Could not find a version that satisfies the requirement torch==2.11.0`.

---

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `POST /meeting/start` continues to return `{"session_id": <uuid>, "status": "active"}` and stores session info in Redis.
- `POST /audio/process` continues to accept WAV uploads, run diarization → transcription → fusion → Qwen reasoning, and return fusion event count.
- `POST /video/process` continues to accept image uploads, run Qwen-VL analysis, and return visual context.
- `POST /meeting/end` continues to invoke DeepSeek refinement and return the full report.
- `GET /meeting/{session_id}` continues to return session info, counts, and report from Redis.
- `POST /trello/sync` continues to read scrum updates from Redis and create Trello cards.
- `GET /trello/lists` continues to return board lists.
- `/ws/{session_id}` WebSocket endpoint continues to accept connections and broadcast scrum updates.
- All Pydantic models (`ScrumUpdate`, `FusionEvent`, `MeetingSession`, `TrelloCard`, etc.) remain structurally unchanged.
- Redis key schema (`session:{session_id}:{key}`) remains unchanged.
- `configs/settings.py` continues to load all configuration from `.env` via `pydantic-settings`.
- Graceful degradation when models fail to load (empty results, no crash).

**Scope:**
All inputs that do NOT trigger any of the 19 bug conditions are completely unaffected by this fix. This includes valid mono WAV audio, valid image uploads, valid WebSocket connections, valid Trello sync requests, and all Redis read/write operations.

---

## Hypothesized Root Cause

### Category 1 — Import & Module Errors (1.1–1.7)

1. **Dead import (1.1)**: `MeetingSession` was imported in `main.py` during early development and never cleaned up. No functional impact but adds noise.
2. **Stale websockets API usage (1.2, 1.4, 1.5)**: `WebSocketServer` was written against the `websockets` v9 API. In v10+, `websockets.serve()` returns `websockets.WebSocketServer` (not `websockets.Server`), raw connections use `await websocket.recv()` (not `.receive_json()`), and disconnects raise `websockets.exceptions.ConnectionClosed` (not FastAPI's `WebSocketDisconnect`).
3. **Dual-manager architecture (1.3)**: The module exports both `websocket_server` (wrapping its own `ConnectionManager`) and a standalone `connection_manager`. `main.py` only imports `connection_manager`, so `WebSocketServer` is never started and its internal manager is never used — the two managers are completely disconnected.
4. **Missing import (1.6)**: `AsyncIterator` was used in `vision_service.process_video_stream` type annotation but only `List` and `Optional` were imported from `typing`.
5. **Wrong tensor shape (1.7)**: `torch.from_numpy(audio_array)` on a 1-D array produces shape `(N,)`. Pyannote's Pipeline requires `(channels, samples)` — the `.unsqueeze(0)` call to add the channel dimension was omitted.

### Category 2 — Logic & Runtime Bugs (1.8–1.13)

6. **Infinite loop in stop_session (1.8)**: `for audio in remaining_audio: await self._align_and_fuse(session_id)` — the loop variable `audio` is never passed to the fuse call. `_align_and_fuse` reads the full buffer on every iteration, so the buffer never drains.
7. **Duplicate fusion events (1.9)**: `_align_and_fuse` reads `self._audio_buffer` and `self._video_buffer` but never removes processed items. Every subsequent call re-processes all previous events.
8. **Empty-string ScrumUpdate (1.10)**: `data.get("task") or ""` converts `None` to `""`. The `if update:` check in `main.py` is truthy for any `ScrumUpdate` object (Pydantic model instances are always truthy), so empty-task updates are stored in Redis.
9. **Missing do_sample=True (1.11)**: HuggingFace Transformers ≥4.35 raises `ValueError` when `temperature != 1.0` and `do_sample` is not explicitly `True`. `_run_refinement` passes `temperature=0.5` without `do_sample=True`.
10. **Stereo WAV not converted to mono (1.12)**: `np.frombuffer(frames, dtype=np.int16)` on a stereo WAV produces an interleaved array of shape `(2N,)`. Pyannote and duration calculation both assume mono `(N,)`.
11. **Silent WebSocket disconnect (1.13)**: `except Exception: connection_manager.disconnect(...)` swallows `WebSocketDisconnect` and all other exceptions without logging, making it impossible to diagnose connection issues.

### Category 3 — Configuration & Environment Bugs (1.14–1.16)

12. **Literal session_stream key (1.14)**: `session_stream: str = "session:{session_id}"` is a plain string default. Python does not interpolate `{session_id}` in string literals — it is stored verbatim. No code in the codebase ever formats this field.
13. **hasattr guard always True (1.15)**: `hasattr(settings, 'pyannote_token')` is always `True` because `pyannote_token: Optional[str] = None` is always defined on the `Settings` class. The guard was intended to check whether the token is set, but it checks for attribute existence instead of truthiness.
14. **Trello auth in JSON body (1.16)**: Trello's REST API requires `key` and `token` as URL query parameters. Sending them in the JSON body causes 401 Unauthorized on all card creation requests.

### Category 4 — Dependency Bugs (1.17–1.19)

15. **Invalid torch version (1.17)**: `torch==2.11.0` does not exist on PyPI. The latest stable release as of early 2025 is `2.2.x`.
16. **Missing typing_extensions (1.18)**: `pydantic-settings==2.1.0` and `transformers==4.36.2` both require `typing_extensions` but it is not pinned in `requirements.txt`, risking version conflicts in constrained environments.
17. **websockets version incompatibility (1.19)**: `websockets==12.0` introduced breaking changes to the connection handler signature. The `WebSocketServer.handle_websocket` method uses the old two-argument `(websocket, path)` signature which is no longer supported in v12.

---

## Correctness Properties

Property 1: Bug Condition — All 19 Defects Are Resolved

_For any_ system state where `isBugCondition` returns `true` (i.e., any of the 19 defects are present), the fixed codebase SHALL eliminate each defect such that: `pip install -r requirements.txt` succeeds, `uvicorn main:app` starts without import errors, all API endpoints process valid inputs without crashing, and no invalid data (empty-task ScrumUpdates, duplicate fusion events) is written to Redis.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19**

Property 2: Preservation — Existing Correct Behavior Is Unchanged

_For any_ input where `isBugCondition` returns `false` (i.e., valid mono WAV audio, valid images, valid WebSocket connections, valid Trello sync requests, valid Redis operations), the fixed code SHALL produce exactly the same behavior as the original code, preserving all API response shapes, Redis key schemas, Pydantic model structures, and service interfaces.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10**

---

## Fix Implementation

### Changes Required

#### File: `requirements.txt`

**Bug 1.17 — Invalid torch version:**
- Change `torch==2.11.0` → `torch==2.2.2`

**Bug 1.18 — Missing typing_extensions:**
- Add `typing_extensions>=4.9.0`

**Bug 1.19 — websockets version incompatibility:**
- Change `websockets==12.0` → `websockets>=10.0,<12.0` (or pin to `websockets==11.0.3` which is the last stable v11 release with the two-argument handler signature)

---

#### File: `main.py`

**Bug 1.1 — Dead import:**
- Remove `MeetingSession` from `from models import MeetingSession, ScrumUpdate, LiveUpdate`
- Result: `from models import ScrumUpdate, LiveUpdate`

**Bug 1.12 — Stereo WAV not converted to mono:**
- After `audio_array = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0`, add:
  ```python
  n_channels = wav.getnchannels()
  if n_channels > 1:
      audio_array = audio_array.reshape(-1, n_channels).mean(axis=1)
  ```
  Note: `wav.getnchannels()` must be called before `wav_file` context exits — move it inside the `with wave.open(...)` block.

**Bug 1.13 — Silent WebSocket disconnect:**
- Replace bare `except Exception:` in `websocket_endpoint` with:
  ```python
  except WebSocketDisconnect:
      connection_manager.disconnect(websocket, session_id)
  except Exception as e:
      print(f"WebSocket error for session {session_id}: {e}")
      connection_manager.disconnect(websocket, session_id)
  ```
- Add `from fastapi import WebSocketDisconnect` to imports (already available via `fastapi`).

---

#### File: `services/websocket_service/__init__.py`

**Bug 1.2 — Wrong websockets Server type:**
- Change `self._server: Optional[websockets.Server] = None` → `self._server: Optional[websockets.WebSocketServer] = None`

**Bug 1.3 — Dual-manager architecture:**
- Remove `websocket_server = WebSocketServer()` from module-level exports.
- Keep only `connection_manager = ConnectionManager()`.
- The `WebSocketServer` class can remain in the file for future use but MUST NOT be instantiated at module level.
- Update `__all__` (if present) accordingly.

**Bug 1.4 — receive_json on raw websockets protocol:**
- In `WebSocketServer.handle_websocket`, replace `data = await websocket.receive_json()` with:
  ```python
  raw = await websocket.recv()
  data = json.loads(raw)
  ```

**Bug 1.5 — Wrong disconnect exception:**
- In `WebSocketServer.handle_websocket`, replace `except WebSocketDisconnect:` with `except websockets.exceptions.ConnectionClosed:`.
- Remove `from fastapi import WebSocket, WebSocketDisconnect` import (or keep `WebSocket` only for type annotations on the FastAPI side).
- Add `import websockets.exceptions` if not already covered by `import websockets`.

---

#### File: `services/vision_service/__init__.py`

**Bug 1.6 — Missing AsyncIterator import:**
- Change `from typing import List, Optional` → `from typing import List, Optional, AsyncIterator`

---

#### File: `services/audio_service/__init__.py`

**Bug 1.7 — Wrong tensor shape for pyannote:**
- In `SpeakerDiarizationService.diarize`, change:
  ```python
  waveform = {"waveform": torch.from_numpy(audio_array), "sample_rate": sample_rate}
  ```
  to:
  ```python
  waveform = {"waveform": torch.from_numpy(audio_array).unsqueeze(0), "sample_rate": sample_rate}
  ```

**Bug 1.15 — hasattr guard always True:**
- In `SpeakerDiarizationService.initialize`, replace:
  ```python
  use_auth_token=settings.pyannote_token if hasattr(settings, 'pyannote_token') else None
  ```
  with:
  ```python
  use_auth_token=settings.pyannote_token if settings.pyannote_token else None
  ```

---

#### File: `services/fusion_service/__init__.py`

**Bug 1.8 — Infinite loop in stop_session:**
- Replace the `stop_session` flush loop:
  ```python
  # BEFORE (broken):
  for audio in remaining_audio:
      await self._align_and_fuse(session_id)

  # AFTER (fixed):
  if remaining_audio:
      await self._align_and_fuse(session_id)
  async with self._buffer_lock:
      self._audio_buffer.clear()
      self._video_buffer.clear()
  ```

**Bug 1.9 — Duplicate fusion events (buffer never cleared):**
- At the end of `_align_and_fuse`, after building `fusion_events`, clear the processed events:
  ```python
  async with self._buffer_lock:
      self._audio_buffer.clear()
      self._video_buffer.clear()
  ```
  Architectural note: since `_align_and_fuse` processes the entire buffer snapshot, clearing after processing is correct. If incremental processing is needed in future, a cursor/offset approach should be used instead.

---

#### File: `services/reasoning_service/__init__.py`

**Bug 1.10 — Empty-string ScrumUpdate:**
- In `_parse_scrum_update`, change:
  ```python
  task=data.get("task") or "",
  ```
  to:
  ```python
  task=data.get("task") or None,
  ```
  Then add a guard before constructing `ScrumUpdate`:
  ```python
  task_value = data.get("task")
  if not task_value or not task_value.strip():
      return None
  ```

**Bug 1.11 — Missing do_sample=True in DeepSeek:**
- In `DeepSeekRefinementService._run_refinement`, add `do_sample=True` to the `model.generate` call:
  ```python
  outputs = self.model.generate(
      inputs,
      max_new_tokens=512,
      temperature=0.5,
      do_sample=True        # ← add this
  )
  ```

---

#### File: `services/trello_service/__init__.py`

**Bug 1.16 — Trello auth in JSON body:**
- In `create_card`, remove `"key"` and `"token"` from the `data` dict:
  ```python
  data = {
      "name": card.name,
      "desc": card.desc or "",
      "idList": card.idList,
      # key and token removed from body
  }
  ```
- Pass auth via `params=` by merging with `_auth_params()`:
  ```python
  async with session.post(
      url,
      json=data,
      params=self._auth_params(),   # ← move auth here
      headers=self._auth_headers()
  ) as response:
  ```

---

#### File: `configs/settings.py`

**Bug 1.14 — Literal session_stream key:**
- Remove the `session_stream` field entirely (it is never used correctly anywhere in the codebase — `core/__init__.py` already constructs session keys as `f"session:{session_id}:{key}"` directly).
- Alternatively, replace with a helper method:
  ```python
  def session_stream_key(self, session_id: str) -> str:
      return f"session:{session_id}"
  ```
  Preferred approach: remove the field to avoid confusion, since `RedisClient` already handles session key formatting internally.

---

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate each bug on the unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bugs BEFORE implementing the fix. Confirm or refute the root cause analysis.

**Test Plan**: Write unit tests that directly invoke the defective code paths with inputs that trigger each bug condition. Run these tests on the UNFIXED code to observe failures.

**Test Cases**:
1. **Tensor shape test (1.7)**: Call `diarize(np.zeros(16000, dtype=np.float32), 16000)` on unfixed code — expect `ValueError` from pyannote about tensor dimensions.
2. **Infinite loop test (1.8)**: Call `stop_session` with 2 buffered audio events and a timeout — expect timeout/hang on unfixed code.
3. **Duplicate events test (1.9)**: Call `process_audio_event` twice and count Redis publish calls — expect 3 publishes (1 + 2) on unfixed code.
4. **Empty task test (1.10)**: Call `_parse_scrum_update('{"task": null, ...}', 0.0)` — expect a `ScrumUpdate` with `task=""` on unfixed code.
5. **do_sample test (1.11)**: Mock `model.generate` to raise `ValueError` when `do_sample` is absent — expect the error to propagate on unfixed code.
6. **Stereo WAV test (1.12)**: Post a stereo WAV to `/audio/process` — expect incorrect duration or diarization error on unfixed code.
7. **Trello auth test (1.16)**: Inspect the `aiohttp` request body in `create_card` — expect `key`/`token` in JSON body on unfixed code.
8. **pip install test (1.17)**: Run `pip install torch==2.11.0` in isolation — expect `ERROR: Could not find a version` on unfixed requirements.

**Expected Counterexamples**:
- Pyannote raises shape error for 1-D tensor input.
- `stop_session` never returns when buffer is non-empty.
- Redis receives N*(N+1)/2 fusion events after N audio events instead of N.
- `_parse_scrum_update` returns non-None with empty task string.
- Transformers raises `ValueError` for temperature without do_sample.

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed code produces the expected behavior.

**Pseudocode:**
```
FOR ALL defect IN [1.1 .. 1.19] DO
  input := minimal_reproducer(defect)
  result := fixed_code(input)
  ASSERT expectedBehavior(result, defect)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed code produces the same result as the original code.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT original_code(input) = fixed_code(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many valid input combinations automatically.
- It catches edge cases that manual unit tests might miss.
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs.

**Test Cases**:
1. **Mono WAV preservation**: Verify mono WAV processing produces the same AudioEvent structure before and after the stereo fix.
2. **Single audio event fusion preservation**: Verify that processing one audio event produces exactly one fusion event (no duplicates) after the buffer-clearing fix.
3. **Valid ScrumUpdate preservation**: Verify that `_parse_scrum_update` with a non-empty task still returns a valid `ScrumUpdate` after the empty-task guard is added.
4. **Trello GET preservation**: Verify `get_board_lists` and `get_cards_in_list` (which already use `params=`) are unaffected by the `create_card` fix.
5. **Settings preservation**: Verify all other `Settings` fields load correctly from `.env` after removing `session_stream`.

### Unit Tests

- Test `SpeakerDiarizationService.diarize` with 1-D and 2-D tensors — only 2-D should succeed.
- Test `FusionEngine.stop_session` completes within a timeout with buffered events.
- Test `FusionEngine.process_audio_event` called N times produces exactly N fusion events in Redis.
- Test `QwenReasoningService._parse_scrum_update` returns `None` for null/empty task.
- Test `DeepSeekRefinementService._run_refinement` passes `do_sample=True` to `model.generate`.
- Test `TrelloService.create_card` sends `key`/`token` as query params, not in JSON body.
- Test `main.py` stereo WAV handling averages channels to mono correctly.
- Test `main.py` WebSocket disconnect logs the session ID and reason.

### Property-Based Tests

- Generate random `np.ndarray` shapes and verify `diarize` always receives a 2-D tensor after the fix.
- Generate random sequences of audio events and verify fusion event count equals input count (no duplicates).
- Generate random LLM response strings and verify `_parse_scrum_update` never returns a `ScrumUpdate` with an empty or whitespace-only task.
- Generate random WAV channel counts (1, 2, 4, 6) and verify the output is always 1-D mono after the channel-averaging fix.

### Integration Tests

- Full startup test: `uvicorn main:app` starts without import errors after all fixes.
- `pip install -r requirements.txt` succeeds with the corrected dependency versions.
- `POST /audio/process` with a stereo WAV returns a valid response without crashing.
- `POST /trello/sync` with mocked Trello API verifies auth is sent as query params.
- WebSocket connect → disconnect cycle logs the disconnect reason correctly.
- Two sequential `POST /audio/process` calls produce exactly 2 fusion events in Redis (not 3).
