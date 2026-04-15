import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime

from models import (
    AudioEvent, 
    VideoEvent, 
    FusionEvent, 
    TranscriptSegment,
    SpeakerSegment
)
from core import get_redis, RedisClient
from configs.settings import settings


class FusionEngine:
    def __init__(self):
        self.redis: Optional[RedisClient] = None
        self._running = False
        self._audio_buffer: List[AudioEvent] = []
        self._video_buffer: List[VideoEvent] = []
        self._buffer_lock = asyncio.Lock()
        self._fusion_task: Optional[asyncio.Task] = None
        self._time_tolerance = 0.5
    
    async def initialize(self):
        self.redis = await get_redis()
    
    async def process_audio_event(self, event: AudioEvent) -> List[FusionEvent]:
        async with self._buffer_lock:
            self._audio_buffer.append(event)
        
        return await self._align_and_fuse(event.session_id)
    
    async def process_video_event(self, event: VideoEvent) -> List[FusionEvent]:
        async with self._buffer_lock:
            self._video_buffer.append(event)
        
        return await self._align_and_fuse(event.session_id)
    
    async def _align_and_fuse(self, session_id: str) -> List[FusionEvent]:
        async with self._buffer_lock:
            audio_events = list(self._audio_buffer)
            video_events = list(self._video_buffer)
        
        fusion_events = []
        
        for audio in audio_events:
            aligned_video = self._find_aligned_video(audio.timestamp, video_events)
            
            speaker = None
            text = None
            if audio.transcripts:
                latest_transcript = max(audio.transcripts, key=lambda t: t.start)
                speaker = latest_transcript.speaker
                text = latest_transcript.text
            
            fusion_event = FusionEvent(
                session_id=session_id,
                timestamp=audio.timestamp,
                speaker=speaker,
                text=text,
                visual_context=aligned_video.visual_context if aligned_video else None,
                audio_source=audio.transcripts[0] if audio.transcripts else None,
                video_source=aligned_video
            )
            
            fusion_events.append(fusion_event)
            
            if self.redis:
                await self.redis.publish_event(
                    settings.fusion_event_stream,
                    fusion_event.model_dump()
                )
                await self.redis.append_to_session_list(
                    session_id, 
                    "fusion_events",
                    fusion_event.model_dump()
                )
        
        async with self._buffer_lock:
            self._audio_buffer.clear()
            self._video_buffer.clear()
        
        return fusion_events
    
    def _find_aligned_video(
        self, 
        audio_timestamp: float, 
        video_events: List[VideoEvent]
    ) -> Optional[VideoEvent]:
        if not video_events:
            return None
        
        min_diff = float('inf')
        aligned_video = None
        
        for video in video_events:
            diff = abs(video.timestamp - audio_timestamp)
            if diff < min_diff and diff <= self._time_tolerance:
                min_diff = diff
                aligned_video = video
        
        return aligned_video
    
    async def get_fused_events(self, session_id: str) -> List[FusionEvent]:
        if not self.redis:
            return []
        
        events_data = await self.redis.get_session_list(session_id, "fusion_events")
        return [FusionEvent(**e) for e in events_data]
    
    async def start_session(self, session_id: str):
        self._running = True
        self._audio_buffer.clear()
        self._video_buffer.clear()
    
    async def stop_session(self, session_id: str):
        self._running = False
        
        async with self._buffer_lock:
            remaining_audio = list(self._audio_buffer)
            remaining_video = list(self._video_buffer)
        
        if remaining_audio:
            await self._align_and_fuse(session_id)
        async with self._buffer_lock:
            self._audio_buffer.clear()
            self._video_buffer.clear()


fusion_engine = FusionEngine()