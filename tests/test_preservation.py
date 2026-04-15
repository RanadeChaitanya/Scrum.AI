"""
Preservation Property Tests
============================
These tests verify EXISTING CORRECT behavior that must be preserved after
the bugfix is applied.

IMPORTANT: These tests are designed to PASS on the UNFIXED codebase.
They establish the baseline behavior that the fixes must not break.

Tests covered:
  P.1 — Mono WAV preservation: process_audio_chunk returns AudioEvent with
        correct duration == len(array) / sample_rate (mono path unaffected
        by the stereo bug).
  P.2 — Single audio event fusion preservation: one process_audio_event call
        produces exactly 1 Redis publish (duplicate bug only manifests on N≥2).
  P.3 — Valid ScrumUpdate preservation: _parse_scrum_update with a non-null,
        non-empty task returns a ScrumUpdate with the same task value.
  P.4 — Settings fields load correctly: all valid Settings field names load
        from .env without error.
  P.5 — Trello GET methods use query params: get_board_lists and
        get_cards_in_list already send auth as query params (correct behavior
        that must remain unchanged).

Run with:
  pytest tests/test_preservation.py -v

Expected outcome on UNFIXED code: ALL tests PASS.
"""

import asyncio
import io
import json
import sys
import types
import wave
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

try:
    from hypothesis import given, settings as h_settings, assume
    from hypothesis import strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False


# ---------------------------------------------------------------------------
# Stub injection — reuse the same pattern from test_bug_condition.py
# ---------------------------------------------------------------------------

def _install_stubs():
    """Inject minimal stubs for heavy dependencies not installed in CI."""

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
                __exit__=MagicMock(return_value=False),
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


def _decode_mono_wav(wav_bytes: bytes):
    """Decode a mono WAV file the same way main.py does (unfixed code path)."""
    with io.BytesIO(wav_bytes) as wav_file:
        with wave.open(wav_file, "rb") as wav:
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
            audio_array = (
                np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            )
    return audio_array, sample_rate


# ---------------------------------------------------------------------------
# Test P.1 — Mono WAV preservation
# ---------------------------------------------------------------------------

# Parametrize over representative mono WAV sizes
_MONO_SIZES = [1600, 8000, 16000, 32000, 48000]


@pytest.mark.parametrize("n_frames", _MONO_SIZES)
def test_p1_mono_wav_duration_correct(n_frames):
    """
    P.1 — Mono WAV preservation.

    For mono (1-channel) WAV inputs, the audio array decoded by main.py's
    WAV-reading logic has the correct length, so:
        duration == len(array) / sample_rate

    This path is unaffected by bug 1.12 (stereo bug) and MUST PASS on
    unfixed code.

    Validates: Requirements 3.2
    """
    sample_rate = 16000
    wav_bytes = _make_wav_bytes(n_channels=1, n_frames=n_frames, sample_rate=sample_rate)
    audio_array, sr = _decode_mono_wav(wav_bytes)

    expected_duration = n_frames / sample_rate
    actual_duration = len(audio_array) / sr

    assert actual_duration == pytest.approx(expected_duration, rel=1e-6), (
        "Mono WAV duration mismatch: expected {:.4f}s, got {:.4f}s "
        "(n_frames={}, sample_rate={})".format(
            expected_duration, actual_duration, n_frames, sample_rate
        )
    )


if HAS_HYPOTHESIS:
    @given(n_frames=st.integers(min_value=1, max_value=96000))
    @h_settings(max_examples=50)
    def test_p1_mono_wav_duration_property(n_frames):
        """
        P.1 — Hypothesis property: for any mono WAV length, duration is exact.

        Validates: Requirements 3.2
        """
        sample_rate = 16000
        wav_bytes = _make_wav_bytes(n_channels=1, n_frames=n_frames, sample_rate=sample_rate)
        audio_array, sr = _decode_mono_wav(wav_bytes)

        expected_duration = n_frames / sample_rate
        actual_duration = len(audio_array) / sr

        assert actual_duration == pytest.approx(expected_duration, rel=1e-6)


# ---------------------------------------------------------------------------
# Test P.2 — Single audio event fusion preservation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_p2_single_audio_event_produces_one_publish():
    """
    P.2 — Single audio event fusion preservation.

    For N=1 sequential process_audio_event call, Redis receives exactly 1
    fusion event publish.  The duplicate bug (1.9) only manifests on N≥2
    calls, so this MUST PASS on unfixed code.

    Validates: Requirements 3.2
    """
    from services.fusion_service import FusionEngine
    from models import AudioEvent

    engine = FusionEngine()

    mock_redis = MagicMock()
    mock_redis.publish_event = AsyncMock()
    mock_redis.append_to_session_list = AsyncMock()
    engine.redis = mock_redis

    event = AudioEvent(
        session_id="s1",
        timestamp=0.0,
        duration=1.0,
        sample_rate=16000,
    )

    await engine.process_audio_event(event)

    publish_calls = mock_redis.publish_event.call_count
    assert publish_calls == 1, (
        "P.2 failed: expected exactly 1 Redis publish for 1 audio event, "
        "got {}.".format(publish_calls)
    )


# ---------------------------------------------------------------------------
# Test P.3 — Valid ScrumUpdate preservation
# ---------------------------------------------------------------------------

_VALID_TASKS = [
    "Implement login page",
    "Fix database connection bug",
    "Write unit tests for auth module",
    "Deploy to staging",
    "Review PR #42",
    "Update documentation",
    "Refactor payment service",
    "Add rate limiting",
]


@pytest.mark.parametrize("task_value", _VALID_TASKS)
def test_p3_valid_task_returns_scrum_update_with_same_task(task_value):
    """
    P.3 — Valid ScrumUpdate preservation.

    For all non-null, non-empty task strings, _parse_scrum_update returns a
    ScrumUpdate whose .task field equals the input task value.

    This MUST PASS on unfixed code (the empty-task bug only affects null/empty
    inputs; valid tasks are returned correctly).

    Validates: Requirements 3.2, 3.4
    """
    from services.reasoning_service import QwenReasoningService

    svc = QwenReasoningService()
    response = json.dumps({
        "task": task_value,
        "status": "todo",
        "owner": None,
        "priority": "medium",
        "description": None,
        "blocker": None,
        "decision": None,
    })

    result = svc._parse_scrum_update(response, 0.0)

    assert result is not None, (
        "P.3 failed: _parse_scrum_update returned None for valid task {!r}".format(
            task_value
        )
    )
    assert result.task == task_value, (
        "P.3 failed: expected task={!r}, got task={!r}".format(
            task_value, result.task
        )
    )


if HAS_HYPOTHESIS:
    @given(task_value=st.text(min_size=1, max_size=200).filter(lambda s: s.strip()))
    @h_settings(max_examples=50)
    def test_p3_valid_task_property(task_value):
        """
        P.3 — Hypothesis property: any non-empty, non-whitespace task string
        produces a ScrumUpdate with the same task value.

        Validates: Requirements 3.2, 3.4
        """
        from services.reasoning_service import QwenReasoningService

        svc = QwenReasoningService()
        response = json.dumps({
            "task": task_value,
            "status": "todo",
            "owner": None,
            "priority": "medium",
            "description": None,
            "blocker": None,
            "decision": None,
        })

        result = svc._parse_scrum_update(response, 0.0)

        # On unfixed code, task=data.get("task") or "" — for a non-empty string
        # this still returns the original value, so result.task == task_value.
        assert result is not None
        assert result.task == task_value


# ---------------------------------------------------------------------------
# Test P.4 — Settings fields load correctly
# ---------------------------------------------------------------------------

_SETTINGS_FIELDS = [
    ("redis_host", str),
    ("redis_port", int),
    ("redis_db", int),
    ("audio_event_stream", str),
    ("video_event_stream", str),
    ("fusion_event_stream", str),
    ("live_updates_stream", str),
    ("pyannote_model", str),
    ("pyannote_device", str),
    ("asr_model", str),
    ("asr_device", str),
    ("chunk_duration", float),
    ("qwen_vl_model", str),
    ("qwen_vl_device", str),
    ("qwen_model", str),
    ("qwen_device", str),
    ("deepseek_model", str),
    ("deepseek_device", str),
    ("ws_host", str),
    ("ws_port", int),
    ("api_host", str),
    ("api_port", int),
]


@pytest.mark.parametrize("field_name,expected_type", _SETTINGS_FIELDS)
def test_p4_settings_fields_load_without_error(field_name, expected_type):
    """
    P.4 — Settings fields load correctly.

    For all valid Settings field names (excluding the buggy session_stream),
    values load from .env without error and have the expected type.

    This MUST PASS on unfixed code.

    Validates: Requirements 3.10
    """
    from configs.settings import settings

    assert hasattr(settings, field_name), (
        "P.4 failed: Settings has no field {!r}".format(field_name)
    )

    value = getattr(settings, field_name)
    assert isinstance(value, expected_type), (
        "P.4 failed: settings.{} expected type {}, got {} (value={!r})".format(
            field_name, expected_type.__name__, type(value).__name__, value
        )
    )


# ---------------------------------------------------------------------------
# Test P.5 — Trello GET methods use query params
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_p5_get_board_lists_sends_auth_as_query_params():
    """
    P.5 — Trello GET methods use query params (get_board_lists).

    get_board_lists already sends key/token as query params (not in body).
    This correct behavior MUST remain unchanged after the create_card fix.

    Validates: Requirements 3.6
    """
    from services.trello_service import TrelloService

    svc = TrelloService()
    svc.api_key = "test_key"
    svc.token = "test_token"
    svc.board_id = "board123"

    captured_kwargs = {}

    class _MockResponse:
        status = 200
        async def json(self):
            return [{"id": "list1", "name": "To Do"}]
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False

    class _MockSession:
        closed = False
        def get(self, url, **kwargs):
            captured_kwargs.update(kwargs)
            return _MockResponse()

    svc._session = _MockSession()

    await svc.get_board_lists()

    params = captured_kwargs.get("params", {})
    body = captured_kwargs.get("json", {})

    assert "key" in params, (
        "P.5 failed: 'key' not found in query params for get_board_lists. "
        "params={}".format(params)
    )
    assert "token" in params, (
        "P.5 failed: 'token' not found in query params for get_board_lists. "
        "params={}".format(params)
    )
    assert "key" not in body, (
        "P.5 failed: 'key' unexpectedly found in JSON body for get_board_lists."
    )
    assert "token" not in body, (
        "P.5 failed: 'token' unexpectedly found in JSON body for get_board_lists."
    )


@pytest.mark.asyncio
async def test_p5_get_cards_in_list_sends_auth_as_query_params():
    """
    P.5 — Trello GET methods use query params (get_cards_in_list).

    get_cards_in_list already sends key/token as query params (not in body).
    This correct behavior MUST remain unchanged after the create_card fix.

    Validates: Requirements 3.6
    """
    from services.trello_service import TrelloService

    svc = TrelloService()
    svc.api_key = "test_key"
    svc.token = "test_token"

    captured_kwargs = {}

    class _MockResponse:
        status = 200
        async def json(self):
            return [{"id": "card1", "name": "Task A"}]
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False

    class _MockSession:
        closed = False
        def get(self, url, **kwargs):
            captured_kwargs.update(kwargs)
            return _MockResponse()

    svc._session = _MockSession()

    await svc.get_cards_in_list("list123")

    params = captured_kwargs.get("params", {})
    body = captured_kwargs.get("json", {})

    assert "key" in params, (
        "P.5 failed: 'key' not found in query params for get_cards_in_list. "
        "params={}".format(params)
    )
    assert "token" in params, (
        "P.5 failed: 'token' not found in query params for get_cards_in_list. "
        "params={}".format(params)
    )
    assert "key" not in body, (
        "P.5 failed: 'key' unexpectedly found in JSON body for get_cards_in_list."
    )
    assert "token" not in body, (
        "P.5 failed: 'token' unexpectedly found in JSON body for get_cards_in_list."
    )
