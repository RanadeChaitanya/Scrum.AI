import asyncio
import numpy as np
from typing import List, Optional, AsyncIterator

from models import SpeakerSegment, TranscriptSegment, AudioEvent
from core import get_redis, RedisClient
from configs.settings import settings


class SpeakerDiarizationService:
    def __init__(self, device: str = "cpu"):
        self.device = device
        self.pipeline = None

    async def initialize(self):
        try:
            import torch
            from pyannote.audio import Pipeline
            self.pipeline = Pipeline.from_pretrained(
                settings.pyannote_model,
                use_auth_token=settings.pyannote_token if settings.pyannote_token else None
            )
            self.pipeline.to(torch.device(self.device))
        except Exception as e:
            print(f"[audio_service] Diarization unavailable: {e}")
            self.pipeline = None

    async def diarize(self, audio_array: np.ndarray, sample_rate: int = 16000) -> List[SpeakerSegment]:
        if not self.pipeline:
            return []
        try:
            import torch
            waveform = {"waveform": torch.from_numpy(audio_array).unsqueeze(0), "sample_rate": sample_rate}
            diarization = self.pipeline(waveform)
            segments = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                segments.append(SpeakerSegment(start=turn.start, end=turn.end, speaker=speaker))
            return segments
        except Exception as e:
            print(f"[audio_service] Diarization error: {e}")
            return []


class ASRService:
    def __init__(self, device: str = "cpu"):
        self.device = device
        self.model = None
        self.processor = None

    async def initialize(self):
        pass

    async def transcribe(
        self,
        audio_array: np.ndarray,
        sample_rate: int = 16000,
        speaker_segments: Optional[List[SpeakerSegment]] = None
    ) -> List[TranscriptSegment]:
        return []


class AudioService:
    def __init__(self):
        self.diarization = SpeakerDiarizationService(settings.pyannote_device)
        self.asr = ASRService(settings.asr_device)
        self.redis: Optional[RedisClient] = None
        self._running = False

    async def initialize(self):
        await self.diarization.initialize()
        await self.asr.initialize()
        self.redis = await get_redis()

    async def process_audio_chunk(
        self,
        audio_array: np.ndarray,
        session_id: str,
        timestamp: float,
        sample_rate: int = 16000
    ) -> AudioEvent:
        speaker_segments = await self.diarization.diarize(audio_array, sample_rate)
        transcripts = await self.asr.transcribe(audio_array, sample_rate, speaker_segments)

        event = AudioEvent(
            session_id=session_id,
            timestamp=timestamp,
            sample_rate=sample_rate,
            duration=len(audio_array) / sample_rate,
            speaker_segments=speaker_segments,
            transcripts=transcripts
        )

        if self.redis:
            try:
                await self.redis.publish_event(settings.audio_event_stream, event.model_dump())
            except Exception as e:
                print(f"[audio_service] Redis publish error: {e}")

        return event

    async def start_session(self, session_id: str):
        self._running = True

    async def stop_session(self, session_id: str):
        self._running = False


audio_service = AudioService()
