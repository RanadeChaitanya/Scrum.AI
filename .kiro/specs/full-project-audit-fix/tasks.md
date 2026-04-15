# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - All 19 Defects Present in Unfixed Codebase
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bugs exist
  - **DO NOT attempt to fix the test or the code when it fails**
  - **GOAL**: Surface counterexamples that demonstrate each defect category exists
  - **Scoped PBT Approach**: Scope each property to the concrete failing case(s) for reproducibility
  - Test 1.7: Call `diarize(np.zeros(16000, dtype=np.float32), 16000)` — expect `ValueError` from pyannote about tensor shape `(16000,)` vs required `(channels, samples)`
  - Test 1.8: Call `fusion_engine.stop_session("s1")` with 2 buffered audio events and a 2-second timeout — expect timeout/hang (infinite loop)
  - Test 1.9: Call `process_audio_event` twice and count Redis publish calls — expect 3 publishes (1 + 2) instead of 2 (duplicate events)
  - Test 1.10: Call `_parse_scrum_update('{"task": null, "status": "todo"}', 0.0)` — expect a `ScrumUpdate` with `task=""` (empty string, not None)
  - Test 1.11: Mock `model.generate` to raise `ValueError` when `do_sample` is absent — expect the error to propagate from `_run_refinement`
  - Test 1.12: Post a stereo WAV (2-channel) to `/audio/process` — expect incorrect duration or diarization shape error
  - Test 1.16: Inspect the `aiohttp` request in `create_card` — expect `key`/`token` in JSON body instead of query params
  - Test 1.17: Attempt `pip install torch==2.11.0` — expect `ERROR: Could not find a version that satisfies the requirement`
  - Run all tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests FAIL (this is correct — it proves the bugs exist)
  - Document counterexamples found (e.g., "diarize raises ValueError: waveform must be 2D", "stop_session hangs with 2 buffered events")
  - Mark task complete when tests are written, run, and failures are documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17, 1.18, 1.19_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Existing Correct Behavior Is Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Observe: mono WAV processing produces a valid `AudioEvent` with correct duration on unfixed code
  - Observe: single `process_audio_event` call produces exactly 1 fusion event on unfixed code
  - Observe: `_parse_scrum_update` with a non-empty task returns a valid `ScrumUpdate` on unfixed code
  - Observe: `get_board_lists` and `get_cards_in_list` already send auth as query params on unfixed code
  - Observe: all `Settings` fields (redis_host, api_port, etc.) load correctly from `.env` on unfixed code
  - Write property-based test: for all mono WAV inputs (1-channel), `process_audio_chunk` returns an `AudioEvent` with `duration == len(array) / sample_rate`
  - Write property-based test: for N sequential `process_audio_event` calls, Redis receives exactly N fusion event publishes (after buffer-clearing fix, but verify baseline for N=1 on unfixed code)
  - Write property-based test: for all non-null, non-empty task strings, `_parse_scrum_update` returns a `ScrumUpdate` with the same task value
  - Write property-based test: for all valid `Settings` field names (excluding `session_stream`), values load from `.env` without error
  - Verify all preservation tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

- [x] 3. Fix Category 1 — Import & Module Errors (bugs 1.1–1.7)

  - [x] 3.1 Remove unused MeetingSession import from main.py (bug 1.1)
    - In `main.py`, change `from models import MeetingSession, ScrumUpdate, LiveUpdate` to `from models import ScrumUpdate, LiveUpdate`
    - Verify no other reference to `MeetingSession` exists in `main.py`
    - _Bug_Condition: "MeetingSession" IN main.py.unused_imports_
    - _Expected_Behavior: main.py imports only models that are actually used_
    - _Preservation: All other imports and startup behavior unchanged_
    - _Requirements: 2.1_

  - [x] 3.2 Fix websockets.Server type annotation to websockets.WebSocketServer (bug 1.2)
    - In `services/websocket_service/__init__.py`, change `self._server: Optional[websockets.Server] = None` to `self._server: Optional[websockets.WebSocketServer] = None`
    - _Bug_Condition: websocket_service uses websockets.Server type annotation (invalid in v10+)_
    - _Expected_Behavior: Type annotation matches the actual return type of websockets.serve()_
    - _Preservation: WebSocketServer class structure and stop() method unchanged_
    - _Requirements: 2.2_

  - [x] 3.3 Remove disconnected WebSocketServer singleton, keep only connection_manager (bug 1.3)
    - In `services/websocket_service/__init__.py`, remove the module-level line `websocket_server = WebSocketServer()`
    - Keep `connection_manager = ConnectionManager()` as the sole exported singleton
    - The `WebSocketServer` class definition may remain in the file for future use
    - _Bug_Condition: Both WebSocketServer and ConnectionManager instantiated as disconnected singletons_
    - _Expected_Behavior: Single connection_manager singleton used by FastAPI /ws/{session_id} endpoint_
    - _Preservation: ConnectionManager interface (connect, disconnect, broadcast, broadcast_scrum_update) unchanged_
    - _Requirements: 2.3_

  - [x] 3.4 Replace websocket.receive_json() with await websocket.recv() + json.loads() (bug 1.4)
    - In `WebSocketServer.handle_websocket`, replace `data = await websocket.receive_json()` with:
      ```python
      raw = await websocket.recv()
      data = json.loads(raw)
      ```
    - _Bug_Condition: handle_websocket calls .receive_json() on raw websockets.WebSocketServerProtocol (no such method)_
    - _Expected_Behavior: Raw websockets connections use .recv() + json.loads() for message parsing_
    - _Preservation: handle_message dispatch logic unchanged_
    - _Requirements: 2.4_

  - [x] 3.5 Replace WebSocketDisconnect with websockets.exceptions.ConnectionClosed (bug 1.5)
    - In `WebSocketServer.handle_websocket`, replace `except WebSocketDisconnect:` with `except websockets.exceptions.ConnectionClosed:`
    - Remove `WebSocketDisconnect` from the `from fastapi import WebSocket, WebSocketDisconnect` import (keep `WebSocket` only if still needed for type annotations, otherwise remove entirely)
    - Ensure `import websockets` already covers `websockets.exceptions`
    - _Bug_Condition: WebSocketDisconnect is a FastAPI exception never raised by raw websockets library_
    - _Expected_Behavior: ConnectionClosed is caught correctly, preventing connection leaks_
    - _Preservation: Error logging and manager.disconnect() call unchanged_
    - _Requirements: 2.5_

  - [x] 3.6 Add AsyncIterator to typing imports in vision_service (bug 1.6)
    - In `services/vision_service/__init__.py`, change `from typing import List, Optional` to `from typing import List, Optional, AsyncIterator`
    - _Bug_Condition: AsyncIterator used as type annotation in process_video_stream but not imported_
    - _Expected_Behavior: NameError eliminated; process_video_stream type annotation resolves correctly_
    - _Preservation: All VisionService methods and behavior unchanged_
    - _Requirements: 2.6_

  - [x] 3.7 Add .unsqueeze(0) to audio tensor in audio_service diarize method (bug 1.7)
    - In `SpeakerDiarizationService.diarize`, change:
      ```python
      waveform = {"waveform": torch.from_numpy(audio_array), "sample_rate": sample_rate}
      ```
      to:
      ```python
      waveform = {"waveform": torch.from_numpy(audio_array).unsqueeze(0), "sample_rate": sample_rate}
      ```
    - _Bug_Condition: torch.from_numpy on 1-D array produces shape (N,); pyannote requires (channels, samples)_
    - _Expected_Behavior: Tensor shape is (1, N) — valid 2-D mono waveform for pyannote pipeline_
    - _Preservation: diarize() return type (List[SpeakerSegment]) and all downstream behavior unchanged_
    - _Requirements: 2.7_

- [x] 4. Fix Category 2 — Logic & Runtime Bugs (bugs 1.8–1.13)

  - [x] 4.1 Fix infinite loop in FusionEngine.stop_session (bug 1.8)
    - In `FusionEngine.stop_session`, replace:
      ```python
      for audio in remaining_audio:
          await self._align_and_fuse(session_id)
      ```
      with:
      ```python
      if remaining_audio:
          await self._align_and_fuse(session_id)
      async with self._buffer_lock:
          self._audio_buffer.clear()
          self._video_buffer.clear()
      ```
    - _Bug_Condition: Loop iterates remaining_audio but calls _align_and_fuse(session_id) without consuming loop variable; buffer never drains_
    - _Expected_Behavior: stop_session flushes remaining buffered events once and clears buffers_
    - _Preservation: stop_session still processes all buffered events before clearing; _align_and_fuse logic unchanged_
    - _Requirements: 2.8_

  - [x] 4.2 Clear audio/video buffers after fusion in _align_and_fuse (bug 1.9)
    - In `FusionEngine._align_and_fuse`, after building `fusion_events` and publishing to Redis, add:
      ```python
      async with self._buffer_lock:
          self._audio_buffer.clear()
          self._video_buffer.clear()
      ```
    - _Bug_Condition: _align_and_fuse reads full buffer but never removes processed items; every call re-fuses all previous events_
    - _Expected_Behavior: After fusion, processed events are cleared; N audio events produce exactly N fusion events total_
    - _Preservation: _align_and_fuse return value (List[FusionEvent]) and Redis publish logic unchanged_
    - _Requirements: 2.9_

  - [x] 4.3 Guard against empty-string task in _parse_scrum_update (bug 1.10)
    - In `QwenReasoningService._parse_scrum_update`, replace `task=data.get("task") or "",` with a guard before constructing `ScrumUpdate`:
      ```python
      task_value = data.get("task")
      if not task_value or not task_value.strip():
          return None
      return ScrumUpdate(
          task=task_value,
          ...
      )
      ```
    - _Bug_Condition: data.get("task") or "" converts None to ""; ScrumUpdate with task="" passes if update: check_
    - _Expected_Behavior: _parse_scrum_update returns None when task is null, empty, or whitespace-only_
    - _Preservation: Valid non-empty task strings still produce correct ScrumUpdate objects_
    - _Requirements: 2.10_

  - [x] 4.4 Add do_sample=True to DeepSeek model.generate call (bug 1.11)
    - In `DeepSeekRefinementService._run_refinement`, add `do_sample=True` to the `model.generate` call:
      ```python
      outputs = self.model.generate(
          inputs,
          max_new_tokens=512,
          temperature=0.5,
          do_sample=True
      )
      ```
    - _Bug_Condition: temperature=0.5 passed without do_sample=True; Transformers >=4.35 raises ValueError_
    - _Expected_Behavior: model.generate call satisfies Transformers API contract; no ValueError raised_
    - _Preservation: Refinement output quality and response parsing logic unchanged_
    - _Requirements: 2.11_

  - [x] 4.5 Convert stereo WAV to mono in main.py process_audio endpoint (bug 1.12)
    - In `main.py` `process_audio`, inside the `with wave.open(wav_file, 'rb') as wav:` block, read `n_channels = wav.getnchannels()` before closing the context, then after building `audio_array` add:
      ```python
      if n_channels > 1:
          audio_array = audio_array.reshape(-1, n_channels).mean(axis=1)
      ```
    - _Bug_Condition: np.frombuffer on stereo WAV produces interleaved (2N,) array; pyannote and duration calc assume mono (N,)_
    - _Expected_Behavior: Stereo (and multi-channel) WAV is averaged to mono before processing_
    - _Preservation: Mono WAV (n_channels=1) path is completely unchanged; AudioEvent duration calculation correct for both_
    - _Requirements: 2.12_

  - [x] 4.6 Add explicit WebSocketDisconnect logging in websocket_endpoint (bug 1.13)
    - In `main.py` `websocket_endpoint`, replace bare `except Exception:` with:
      ```python
      from fastapi import WebSocketDisconnect
      ...
      except WebSocketDisconnect:
          connection_manager.disconnect(websocket, session_id)
      except Exception as e:
          print(f"WebSocket error for session {session_id}: {e}")
          connection_manager.disconnect(websocket, session_id)
      ```
    - Add `WebSocketDisconnect` to the existing `from fastapi import ...` import at the top of `main.py`
    - _Bug_Condition: bare except Exception swallows WebSocketDisconnect without logging session or reason_
    - _Expected_Behavior: WebSocketDisconnect is caught explicitly; all exceptions log session_id and reason_
    - _Preservation: connection_manager.disconnect() still called on all disconnect paths_
    - _Requirements: 2.13_

- [x] 5. Fix Category 3 — Configuration & Environment Bugs (bugs 1.14–1.16)

  - [x] 5.1 Remove/replace literal session_stream field in settings.py (bug 1.14)
    - In `configs/settings.py`, remove the `session_stream: str = "session:{session_id}"` field entirely
    - Optionally add a helper method instead:
      ```python
      def session_stream_key(self, session_id: str) -> str:
          return f"session:{session_id}"
      ```
    - Preferred: remove the field since `core/__init__.py` already constructs session keys as `f"session:{session_id}:{key}"` directly
    - Verify no code references `settings.session_stream` anywhere in the codebase
    - _Bug_Condition: session_stream is a plain str default with unformatted {session_id} placeholder; never substituted_
    - _Expected_Behavior: Session-scoped Redis keys are formatted dynamically at call sites_
    - _Preservation: All other Settings fields and .env loading behavior unchanged_
    - _Requirements: 2.14_

  - [x] 5.2 Fix pyannote_token guard from hasattr to truthiness check (bug 1.15)
    - In `SpeakerDiarizationService.initialize`, replace:
      ```python
      use_auth_token=settings.pyannote_token if hasattr(settings, 'pyannote_token') else None
      ```
      with:
      ```python
      use_auth_token=settings.pyannote_token if settings.pyannote_token else None
      ```
    - _Bug_Condition: hasattr(settings, 'pyannote_token') is always True; passes None to use_auth_token when token not set_
    - _Expected_Behavior: Token is only passed when it has a truthy value; None/.env-absent case correctly passes None_
    - _Preservation: When pyannote_token IS set in .env, it is still passed correctly to Pipeline.from_pretrained_
    - _Requirements: 2.15_

  - [x] 5.3 Move Trello key/token from JSON body to query params (bug 1.16)
    - In `TrelloService.create_card`, remove `"key": self.api_key` and `"token": self.token` from the `data` dict
    - Add `params=self._auth_params()` to the `session.post(...)` call:
      ```python
      async with session.post(
          url,
          json=data,
          params=self._auth_params(),
          headers=self._auth_headers()
      ) as response:
      ```
    - _Bug_Condition: key/token sent in JSON body; Trello REST API requires them as URL query parameters_
    - _Expected_Behavior: create_card sends auth as query params; Trello returns 200 instead of 401_
    - _Preservation: get_board_lists, get_cards_in_list, update_card (already use params=) are completely unchanged_
    - _Requirements: 2.16_

- [x] 6. Fix Category 4 — Dependency Bugs (bugs 1.17–1.19)

  - [x] 6.1 Fix torch version from 2.11.0 to 2.2.2 (bug 1.17)
    - In `requirements.txt`, change `torch==2.11.0` to `torch==2.2.2`
    - _Bug_Condition: torch==2.11.0 does not exist on PyPI; pip install fails entirely_
    - _Expected_Behavior: pip install -r requirements.txt succeeds with a valid torch version_
    - _Preservation: All other pinned versions unchanged_
    - _Requirements: 2.17_

  - [x] 6.2 Add typing_extensions>=4.9.0 to requirements.txt (bug 1.18)
    - In `requirements.txt`, add `typing_extensions>=4.9.0` under the Utilities section
    - _Bug_Condition: typing_extensions not pinned; pydantic-settings==2.1.0 and transformers==4.36.2 require it_
    - _Expected_Behavior: typing_extensions is explicitly pinned, preventing version conflicts in constrained environments_
    - _Preservation: All other dependency pins unchanged_
    - _Requirements: 2.18_

  - [x] 6.3 Pin websockets to compatible version (bug 1.19)
    - In `requirements.txt`, change `websockets==12.0` to `websockets==11.0.3`
    - websockets v11 is the last stable release supporting the two-argument `(websocket, path)` handler signature used in `WebSocketServer.handle_websocket`
    - _Bug_Condition: websockets==12.0 broke the two-argument connection handler signature used in websocket_service_
    - _Expected_Behavior: websockets==11.0.3 is compatible with the existing handler signature_
    - _Preservation: ConnectionManager (FastAPI WebSocket) is unaffected by websockets library version_
    - _Requirements: 2.19_

- [x] 7. Verify bug condition exploration test now passes

  - [x] 7.1 Re-run bug condition exploration tests after all fixes
    - **Property 1: Expected Behavior** - All 19 Defects Resolved
    - **IMPORTANT**: Re-run the SAME tests from task 1 — do NOT write new tests
    - The tests from task 1 encode the expected behavior for each defect
    - When these tests pass, it confirms the expected behavior is satisfied for all 19 bugs
    - Run all exploration tests from step 1 on the FIXED codebase
    - **EXPECTED OUTCOME**: All tests PASS (confirms all 19 bugs are fixed)
    - Verify: `diarize` no longer raises ValueError for 1-D input (unsqueeze fix)
    - Verify: `stop_session` completes within timeout with buffered events (loop fix)
    - Verify: 2 `process_audio_event` calls produce exactly 2 Redis publishes (buffer-clear fix)
    - Verify: `_parse_scrum_update` with null task returns None (empty-task guard)
    - Verify: `_run_refinement` no longer raises ValueError (do_sample fix)
    - Verify: stereo WAV produces correct mono array (channel-averaging fix)
    - Verify: `create_card` sends auth as query params (Trello fix)
    - Verify: `pip install torch==2.2.2` succeeds (torch version fix)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19_

  - [x] 7.2 Verify preservation tests still pass after all fixes
    - **Property 2: Preservation** - Existing Correct Behavior Is Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run all preservation property tests from step 2 on the FIXED codebase
    - **EXPECTED OUTCOME**: All tests PASS (confirms no regressions)
    - Verify: mono WAV processing still produces correct AudioEvent duration
    - Verify: single audio event still produces exactly 1 fusion event
    - Verify: non-empty task strings still produce valid ScrumUpdate objects
    - Verify: get_board_lists and get_cards_in_list still work correctly
    - Verify: all Settings fields still load from .env correctly
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

- [x] 8. Generate professional README.md
  - Replace the existing `README.md` with a comprehensive, professional document covering:
    - Project overview: AI Scrum Automation System — real-time multimodal meeting analysis
    - Architecture diagram (text-based): FastAPI → Audio/Vision/Fusion/Reasoning/Trello services → Redis
    - Prerequisites: Python 3.10+, Redis, CUDA GPU (optional), Pyannote HuggingFace token, Trello API credentials
    - Installation: `pip install -r requirements.txt`, `.env` setup with all required fields
    - Configuration: document all `Settings` fields from `configs/settings.py` with descriptions and defaults
    - Running the server: `uvicorn main:app --host 0.0.0.0 --port 8000`
    - API reference: all endpoints (`POST /meeting/start`, `POST /audio/process`, `POST /video/process`, `POST /meeting/end`, `GET /meeting/{session_id}`, `POST /trello/sync`, `GET /trello/lists`, `WS /ws/{session_id}`)
    - WebSocket usage example
    - Graceful degradation behavior (models fail to load → empty results, no crash)
    - Known limitations and future work
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [x] 9. Checkpoint — Ensure all tests pass
  - Run the full test suite: `pytest tests/ -v`
  - Verify `pip install -r requirements.txt` completes without errors
  - Verify `python -c "import main"` completes without ImportError
  - Verify all 19 bug condition exploration tests pass
  - Verify all preservation property tests pass
  - Confirm no regressions in API endpoint behavior
  - Ensure all tests pass; ask the user if questions arise
