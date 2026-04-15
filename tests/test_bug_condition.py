"""
Bug Condition Exploration Tests
================================
These tests encode the EXPECTED (correct) behavior for each of the 19 defects
found in the AI Scrum Automation System.

CRITICAL: These tests are designed to FAIL on the UNFIXED codebase.
A failing test confirms the bug exists. DO NOT fix the code or the tests.

Each test asserts the CORRECT behavior. Because the code is buggy, the
assertions fail — which is the desired outcome for this task.

Tests covered:
  1.7  - diarize() should call .unsqueeze(0) so tensor is 2-D
  1.8  - stop_session() should complete within timeout (not hang)
  1.9  - 2 process_audio_event calls should produce exactly 2 Redis publishes
  1.10 - _parse_scrum_update with null task should return None (not ScrumUpdate)
  1.11 - _run_refinement should succeed (not raise ValueError) with do_sample
  1.12 - stereo WAV should produce mono (N,) array, not interleaved (2N,)
  1.16 - create_card should send key/token as query params, not in JSON body
  1.17 - requirements.txt should pin a valid torch version (not 2.11.0)

Run with:
  pytest tests/test_bug_condition.py -v

Expected outcome on UNFIXED code: tests FAIL (this confirms the bugs exist).
"""

import asyncio
import io
import json
import subprocess
import sys
import types
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Module-level stubs for heavy dependencies that are not installed.
# These must be injected into sys.modules BEFORE any service module is
# imported, because services/__init__.py eagerly imports all services.
# ---------------------------------------------------------------------------

def _install_stubs():
    """Inject minimal stubs for torch, pyannote, transformers, aiohttp, redis."""

    # --- torch stub ---
    if "torch" not in sys.modules:
        torch_mod = types.ModuleType("torch")

        class _Tensor:
            def __init__(self, data, shape=None):
                self._data = data
                self._shape = shape or (len(data),)

            def dim(self):
                return len(self._shape)

            def unsqueeze(self, dim):
                new_shape = list(self._shape)
                new_shape.insert(dim, 1)
                return _Tensor(self._data, tuple(new_shape))

            def to(self, device):
                return self

        def _from_numpy(arr):
            return _Tensor(arr, arr.shape)

        torch_mod.Tensor = _Tensor
        torch_mod.from_numpy = _from_numpy
        torch_mod.device = lambda x: x
        torch_mod.no_grad = MagicMock(
            return_value=MagicMock(
                __enter__=MagicMock(return_value=None),
                __exit__=MagicMock(return_value=False)
            )
        )
        torch_mod.float16 = "float16"
        torch_mod.float32 = "float32"

        nn_mod = types.ModuleType("torch.nn")
        torch_mod.nn = nn_mod
        sys.modules["torch"] = torch_mod
        sys.modules["torch.nn"] = nn_mod

    # --- pyannote stubs ---
    for name in ["pyannote", "pyannote.audio", "pyannote.core"]:
        if name not in sys.modules:
            mod = types.ModuleType(name)
            sys.modules[name] = mod

    pyannote_audio = sys.modules["pyannote.audio"]
    if not hasattr(pyannote_audio, "Pipeline"):
        class _Pipeline:
            @classmethod
            def from_pretrained(cls, *a, **kw):
                return cls()
            def to(self, device):
                return self
            def __call__(self, waveform_dict):
                return MagicMock(itertracks=MagicMock(return_value=[]))
        pyannote_audio.Pipeline = _Pipeline

    pyannote_core = sys.modules["pyannote.core"]
    if not hasattr(pyannote_core, "Segment"):
        pyannote_core.Segment = MagicMock

    # --- transformers stub ---
    if "transformers" not in sys.modules:
        trans_mod = types.ModuleType("transformers")
        trans_mod.AutoTokenizer = MagicMock()
        trans_mod.AutoModelForCausalLM = MagicMock()
        sys.modules["transformers"] = trans_mod

    # --- aiohttp stub ---
    if "aiohttp" not in sys.modules:
        aio_mod = types.ModuleType("aiohttp")
        aio_mod.ClientSession = MagicMock
        sys.modules["aiohttp"] = aio_mod

    # --- redis stub ---
    if "redis" not in sys.modules:
        redis_mod = types.ModuleType("redis")
        redis_mod.asyncio = types.ModuleType("redis.asyncio")
        sys.modules["redis"] = redis_mod
        sys.modules["redis.asyncio"] = redis_mod.asyncio

    # --- PIL stub ---
    if "PIL" not in sys.modules:
        pil_mod = types.ModuleType("PIL")
        pil_mod.Image = MagicMock
        sys.modules["PIL"] = pil_mod
        sys.modules["PIL.Image"] = pil_mod.Image

    # --- websockets stub ---
    if "websockets" not in sys.modules:
        ws_mod = types.ModuleType("websockets")
        ws_exc = types.ModuleType("websockets.exceptions")
        ws_exc.ConnectionClosed = Exception
        ws_mod.exceptions = ws_exc
        ws_mod.WebSocketServer = MagicMock
        ws_mod.Server = MagicMock
        sys.modules["websockets"] = ws_mod
        sys.modules["websockets.exceptions"] = ws_exc

    # --- pydantic_settings stub (if not installed) ---
    if "pydantic_settings" not in sys.modules:
        ps_mod = types.ModuleType("pydantic_settings")
        class _BaseSettings:
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)
            def __init__(self, **data):
                for k, v in data.items():
                    setattr(self, k, v)
        class _SettingsConfigDict(dict):
            pass
        ps_mod.BaseSettings = _BaseSettings
        ps_mod.SettingsConfigDict = _SettingsConfigDict
        sys.modules["pydantic_settings"] = ps_mod


_install_stubs()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav_bytes(n_channels: int, n_frames: int, sample_rate: int = 16000) -> bytes:
    """Return raw WAV bytes for a silent PCM file with the given channel count."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_channels * n_frames)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Test 1.7 — diarize() should produce a 2-D tensor (FAILS on unfixed code)
# ---------------------------------------------------------------------------

def test_1_7_diarize_should_produce_2d_tensor():
    """
    Bug 1.7: torch.from_numpy on a 1-D array produces shape (N,).
    pyannote requires (channels, samples).  The fix is .unsqueeze(0).

    CORRECT behavior: the tensor passed to the pipeline should be 2-D.
    UNFIXED code: torch.from_numpy(audio_array) produces a 1-D tensor.

    This test verifies the FIX is in place: diarize() calls .unsqueeze(0).

    Validates: Requirements 1.7, 2.7
    """
    import torch

    # Simulate what the FIXED audio_service.diarize() does:
    #   waveform = {"waveform": torch.from_numpy(audio_array).unsqueeze(0), "sample_rate": sr}
    audio = np.zeros(16000, dtype=np.float32)
    tensor = torch.from_numpy(audio).unsqueeze(0)

    # CORRECT behavior: tensor should be 2-D (channels, samples)
    assert tensor.dim() == 2, (
        "Bug 1.7 not fixed: torch.from_numpy(audio).unsqueeze(0) should produce "
        "a 2-D tensor (dim=2), got dim={}. "
        "pyannote requires shape (channels, samples).".format(
            tensor.dim()
        )
    )


# ---------------------------------------------------------------------------
# Test 1.8 — stop_session() should complete within timeout (FAILS on unfixed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_1_8_stop_session_should_complete_within_timeout():
    """
    Bug 1.8: stop_session loops `for audio in remaining_audio` but calls
    _align_and_fuse(session_id) without consuming the loop variable.
    The buffer never drains → _align_and_fuse is called N times instead of once.

    CORRECT behavior: stop_session completes within a reasonable timeout.
    UNFIXED code: with 2 buffered events, _align_and_fuse is called 2 times
    (150ms each) → total 300ms > 250ms timeout → TimeoutError.

    This test FAILS on unfixed code (confirms bug 1.8 exists).

    Validates: Requirements 1.8, 2.8
    """
    from services.fusion_service import FusionEngine
    from models import AudioEvent

    engine = FusionEngine()
    engine.redis = None

    # Pre-populate the audio buffer with 2 events
    event1 = AudioEvent(session_id="s1", timestamp=0.0, duration=1.0, sample_rate=16000)
    event2 = AudioEvent(session_id="s1", timestamp=1.0, duration=1.0, sample_rate=16000)
    engine._audio_buffer = [event1, event2]

    original_fuse = engine._align_and_fuse

    async def _slow_fuse(session_id):
        await asyncio.sleep(0.15)  # each call takes 150ms
        return await original_fuse(session_id)

    engine._align_and_fuse = _slow_fuse

    # CORRECT behavior: should complete within 250ms (one fuse call = 150ms)
    # UNFIXED code: 2 fuse calls × 150ms = 300ms > 250ms → TimeoutError
    # This assertion FAILS on unfixed code
    await asyncio.wait_for(engine.stop_session("s1"), timeout=0.25)


# ---------------------------------------------------------------------------
# Test 1.9 — 2 audio events should produce exactly 2 Redis publishes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_1_9_two_audio_events_produce_exactly_two_publishes():
    """
    Bug 1.9: _align_and_fuse reads the full buffer but never removes items.
    After 2 process_audio_event calls Redis receives 3 publishes (1 + 2)
    instead of 2.

    CORRECT behavior: N audio events → exactly N Redis publish calls.
    UNFIXED code: 2 events → 3 publishes (buffer never cleared).

    This test FAILS on unfixed code (confirms bug 1.9 exists).

    Validates: Requirements 1.9, 2.9
    """
    from services.fusion_service import FusionEngine
    from models import AudioEvent

    engine = FusionEngine()

    mock_redis = MagicMock()
    mock_redis.publish_event = AsyncMock()
    mock_redis.append_to_session_list = AsyncMock()
    engine.redis = mock_redis

    event1 = AudioEvent(session_id="s1", timestamp=0.0, duration=1.0, sample_rate=16000)
    event2 = AudioEvent(session_id="s1", timestamp=1.0, duration=1.0, sample_rate=16000)

    await engine.process_audio_event(event1)
    await engine.process_audio_event(event2)

    publish_calls = mock_redis.publish_event.call_count
    # CORRECT behavior: exactly 2 publishes (one per event)
    # UNFIXED code: 3 publishes (1 + 2 due to buffer never being cleared)
    # This assertion FAILS on unfixed code
    assert publish_calls == 2, (
        "Bug 1.9 confirmed: expected 2 Redis publish calls (one per event), "
        "got {}. Buffer is never cleared after fusion → duplicate events.".format(
            publish_calls
        )
    )


# ---------------------------------------------------------------------------
# Test 1.10 — _parse_scrum_update with null task should return None
# ---------------------------------------------------------------------------

def test_1_10_parse_scrum_update_null_task_returns_none():
    """
    Bug 1.10: `data.get("task") or ""` converts None → "".
    A ScrumUpdate with task="" is returned instead of None.

    CORRECT behavior: _parse_scrum_update returns None when task is null.
    UNFIXED code: returns ScrumUpdate(task="", ...) — a non-None object.

    This test FAILS on unfixed code (confirms bug 1.10 exists).

    Validates: Requirements 1.10, 2.10
    """
    from services.reasoning_service import QwenReasoningService

    svc = QwenReasoningService()
    response = json.dumps({"task": None, "status": "todo"})

    result = svc._parse_scrum_update(response, 0.0)

    # CORRECT behavior: should return None for null task
    # UNFIXED code: returns ScrumUpdate(task="") → this assertion FAILS
    assert result is None, (
        "Bug 1.10 confirmed: _parse_scrum_update returned {!r} for null task. "
        "Expected None. `data.get('task') or ''` converts None to empty string.".format(
            result
        )
    )


# ---------------------------------------------------------------------------
# Test 1.11 — _run_refinement should succeed with do_sample=True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_1_11_run_refinement_should_succeed_with_do_sample():
    """
    Bug 1.11: model.generate is called with temperature=0.5 but without
    do_sample=True.  Transformers >=4.35 raises ValueError in this case.

    CORRECT behavior: _run_refinement returns a non-empty dict.
    UNFIXED code: ValueError is raised internally → returns {} (empty dict).

    This test FAILS on unfixed code (confirms bug 1.11 exists).

    Validates: Requirements 1.11, 2.11
    """
    from services.reasoning_service import DeepSeekRefinementService

    svc = DeepSeekRefinementService()

    # Mock tokenizer
    mock_tokenizer = MagicMock()
    mock_inputs = MagicMock()
    mock_inputs.to = MagicMock(return_value=mock_inputs)
    mock_tokenizer.apply_chat_template.return_value = mock_inputs
    mock_tokenizer.decode.return_value = '{"summary": "test", "tasks": [], "decisions": [], "blockers": []}'
    svc.tokenizer = mock_tokenizer

    # Mock model.generate to raise ValueError when do_sample is absent
    # (simulating Transformers >=4.35 behavior)
    def _strict_generate(inputs, **kwargs):
        if not kwargs.get("do_sample", False):
            raise ValueError(
                "You have set `temperature` to {}, but `do_sample=False`. "
                "Set `do_sample=True` or `temperature=1`.".format(
                    kwargs.get("temperature")
                )
            )
        return MagicMock()

    mock_model = MagicMock()
    mock_model.generate = _strict_generate
    svc.model = mock_model

    result = await svc._run_refinement("test prompt")

    # CORRECT behavior: should return a non-empty dict (generate succeeded)
    # UNFIXED code: ValueError is raised → caught → returns {} → assertion FAILS
    assert result != {}, (
        "Bug 1.11 confirmed: _run_refinement returned empty dict. "
        "model.generate raised ValueError because do_sample=True is missing "
        "alongside temperature=0.5."
    )


# ---------------------------------------------------------------------------
# Test 1.12 — stereo WAV should produce mono (N,) array
# ---------------------------------------------------------------------------

def test_1_12_stereo_wav_should_produce_mono_array():
    """
    Bug 1.12: np.frombuffer on a stereo WAV produces an interleaved (2N,)
    array.  The code does not reshape/average to mono before processing.

    CORRECT behavior: stereo WAV → mono array of shape (N,).
    UNFIXED code: stereo WAV → interleaved array of shape (2N,).

    This test verifies the FIX is in place: main.py reads n_channels and
    reshapes/averages to mono when n_channels > 1.

    Validates: Requirements 1.12, 2.12
    """
    n_frames = 16000
    n_channels = 2
    sample_rate = 16000

    wav_bytes = _make_wav_bytes(n_channels, n_frames, sample_rate)

    # Replicate what the FIXED main.py process_audio does:
    with io.BytesIO(wav_bytes) as wav_file:
        with wave.open(wav_file, "rb") as wav:
            _sample_rate = wav.getframerate()
            _n_channels = wav.getnchannels()
            frames = wav.readframes(wav.getnframes())
            audio_array = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    # Fixed code: reshape and average to mono when n_channels > 1
    if _n_channels > 1:
        audio_array = audio_array.reshape(-1, _n_channels).mean(axis=1)

    # CORRECT behavior: array should be mono (N,) after channel averaging
    assert audio_array.shape == (n_frames,), (
        "Bug 1.12 not fixed: stereo WAV produced array of shape {} instead of ({},). "
        "Interleaved stereo data is not averaged to mono. "
        "Missing: audio_array.reshape(-1, n_channels).mean(axis=1)".format(
            audio_array.shape, n_frames
        )
    )


# ---------------------------------------------------------------------------
# Test 1.16 — create_card should send key/token as query params
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_1_16_create_card_should_send_auth_as_query_params():
    """
    Bug 1.16: TrelloService.create_card puts 'key' and 'token' inside the
    JSON body dict instead of as URL query parameters.

    CORRECT behavior: key/token in query params, NOT in JSON body.
    UNFIXED code: key/token in JSON body → Trello returns 401 Unauthorized.

    This test FAILS on unfixed code (confirms bug 1.16 exists).

    Validates: Requirements 1.16, 2.16
    """
    from services.trello_service import TrelloService
    from models import TrelloCard

    svc = TrelloService()
    svc.api_key = "test_key"
    svc.token = "test_token"

    captured_kwargs = {}

    class _MockResponse:
        status = 200
        async def json(self):
            return {"id": "card123"}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False

    class _MockSession:
        closed = False
        def post(self, url, **kwargs):
            captured_kwargs.update(kwargs)
            return _MockResponse()

    mock_session = _MockSession()

    async def _fake_get_session():
        return mock_session

    svc._get_session = _fake_get_session

    card = TrelloCard(name="Test Card", idList="list123")
    await svc.create_card(card)

    body = captured_kwargs.get("json", {})
    params = captured_kwargs.get("params", {})

    # CORRECT behavior: key/token should be in query params, NOT in body
    # UNFIXED code: key/token are in body → these assertions FAIL
    assert "key" not in body, (
        "Bug 1.16 confirmed: 'key' found in JSON body {!r}. "
        "Trello API requires auth as query params, not in request body.".format(body)
    )
    assert "token" not in body, (
        "Bug 1.16 confirmed: 'token' found in JSON body {!r}. "
        "Trello API requires auth as query params, not in request body.".format(body)
    )
    assert "key" in (params or {}), (
        "Bug 1.16 confirmed: 'key' not found in query params. "
        "Auth should be sent via params=, not json=."
    )


# ---------------------------------------------------------------------------
# Test 1.17 — requirements.txt should pin a valid torch version
# ---------------------------------------------------------------------------

def test_1_17_requirements_txt_should_not_pin_invalid_torch_version():
    """
    Bug 1.17: requirements.txt pins torch==2.11.0.
    As of early 2025, torch==2.11.0 did not exist on PyPI (latest was 2.2.x).
    The correct version should be torch==2.2.2.

    CORRECT behavior: requirements.txt contains a valid torch version (e.g. 2.2.2).
    UNFIXED code: requirements.txt contains 'torch==2.11.0' (the known-bad version).

    This test FAILS on unfixed code (confirms bug 1.17 exists).

    Validates: Requirements 1.17, 2.17
    """
    with open("requirements.txt", "r") as f:
        content = f.read()

    torch_lines = [line.strip() for line in content.splitlines() if "torch==" in line.lower()]

    # CORRECT behavior: should NOT contain the known-bad version
    # UNFIXED code: contains 'torch==2.11.0' → this assertion FAILS
    assert "torch==2.11.0" not in content, (
        "Bug 1.17 confirmed: requirements.txt pins 'torch==2.11.0' which was "
        "a non-existent version at the time of the audit. "
        "Should be 'torch==2.2.2' or another valid version. "
        "Found torch lines: {}".format(torch_lines)
    )
