from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # ✅ IMPORTANT: Pydantic v2 config style
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"   # 🔥 prevents crash from unexpected env vars
    )

    # Redis Configuration
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None

    # Redis Stream Keys
    audio_event_stream: str  = "audio:events"
    video_event_stream: str  = "video:events"
    fusion_event_stream: str = "fusion:events"
    live_updates_stream: str = "live:updates"

    # Redis Pub/Sub channel — all worker outputs fan out here → WebSocket broadcaster
    scrum_pubsub_channel: str = "scrum_updates"

    # Audio Service
    pyannote_model: str = "pyannote/speaker-diarization-3.1"
    pyannote_device: str = "cuda"
    asr_model: str = "Parakeet-ASR"
    asr_device: str = "cuda"
    chunk_duration: float = 3.0

    # 🔥 FIX: ADD THIS (your crash was from this missing field)
    pyannote_token: Optional[str] = None

    # Vision Service
    qwen_vl_model: str = "Qwen/Qwen2-VL-2B-Instruct"
    qwen_vl_device: str = "cuda"
    video_sample_interval: float = 1.0

    # Reasoning Service
    qwen_model: str = "Qwen/Qwen2.5-7B-Instruct"
    qwen_device: str = "cuda"
    deepseek_model: str = "deepseek-ai/DeepSeek-R1"
    deepseek_device: str = "cuda"

    # Trello Integration
    trello_api_key: Optional[str] = None
    trello_token: Optional[str] = None
    trello_board_id: Optional[str] = None

    # Base URL used for OAuth callbacks — must exactly match the
    # "Allowed Origins" / "Redirect URL" registered in the Trello Developer Console.
    # https://trello.com/app-key  →  "Allowed Origins" field
    app_base_url: str = "http://localhost:8000"

    # Fallback URL — where to redirect on any auth failure
    trello_fallback_url: str = "/auth/error"

    # WebSocket
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000


# global singleton
settings = Settings()