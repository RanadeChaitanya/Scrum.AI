import asyncio
import numpy as np
from typing import List, Optional, AsyncIterator

from models import VideoEvent
from core import get_redis, RedisClient
from configs.settings import settings


class VisionService:
    def __init__(self):
        self.model = None
        self.processor = None
        self.redis: Optional[RedisClient] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def initialize(self):
        try:
            import torch
            from transformers import AutoProcessor, AutoModelForVision2Seq
            self.processor = AutoProcessor.from_pretrained(settings.qwen_vl_model)
            self.model = AutoModelForVision2Seq.from_pretrained(
                settings.qwen_vl_model,
                torch_dtype=torch.float16 if self._get_device() == "cuda" else torch.float32
            )
            self.model.to(self._get_device())
        except Exception as e:
            print(f"[vision_service] Model unavailable: {e}")

        self.redis = await get_redis()

    def _get_device(self) -> str:
        return settings.qwen_vl_device

    async def analyze_frame(self, image, session_id: str, timestamp: float, prompt: Optional[str] = None) -> VideoEvent:
        if not self.model or not self.processor:
            return VideoEvent(session_id=session_id, timestamp=timestamp, descriptions=[])

        if prompt is None:
            prompt = (
                "Analyze this meeting screenshot. Describe what is being shown: "
                "slides, dashboards, diagrams, code, tasks, or any visual Scrum context. "
                "Respond with a brief description."
            )

        try:
            import torch
            inputs = self.processor(images=image, text=prompt, return_tensors="pt").to(self._get_device())
            with torch.no_grad():
                output = self.model.generate(**inputs, max_new_tokens=128)
            description = self.processor.decode(output[0], skip_special_tokens=True)

            event = VideoEvent(
                session_id=session_id,
                timestamp=timestamp,
                descriptions=[description] if description else [],
                visual_context=description
            )

            if self.redis:
                try:
                    await self.redis.publish_event(settings.video_event_stream, event.model_dump())
                except Exception as e:
                    print(f"[vision_service] Redis publish error: {e}")

            return event
        except Exception as e:
            print(f"[vision_service] Analysis error: {e}")
            return VideoEvent(session_id=session_id, timestamp=timestamp, descriptions=[])

    async def process_video_stream(self, image_iterator, session_id: str):
        self._running = True
        interval = settings.video_sample_interval
        while self._running:
            try:
                image = await asyncio.wait_for(image_iterator.__anext__(), timeout=interval + 1)
                await self.analyze_frame(image, session_id, 0.0)
                await asyncio.sleep(interval)
            except (asyncio.TimeoutError, StopAsyncIteration):
                break
            except Exception as e:
                print(f"[vision_service] Stream error: {e}")
                break

    async def start_session(self, session_id: str):
        self._running = True

    async def stop_session(self, session_id: str):
        self._running = False
        if self._task:
            self._task.cancel()


vision_service = VisionService()
