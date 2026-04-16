"""
Vision Worker — consumes vision jobs from Redis queue.
Runs: Qwen-VL frame analysis → Fusion → publishes structured visual_context event.

Output event format:
{
  "event_type":  "visual_context",
  "meeting_id":  "<session_id>",
  "source":      "vision_worker",
  "timestamp":   "<utc_iso>",
  "content":     "<visual description>",
  "priority":    "low",
  "data":        { visual_context, descriptions }
}
"""
import base64
import io
from datetime import datetime, timezone
from typing import Any, Dict

from workers.base import BaseWorker
from core import redis_client
from utils.time import now_iso


class VisionWorker(BaseWorker):
    queue_key   = "jobs:vision"
    worker_name = "vision_worker"

    async def process(self, job: Dict[str, Any]) -> None:
        session_id = job["session_id"]
        timestamp  = job.get("timestamp", 0.0)
        img_b64    = job.get("image_b64", "")

        if not img_b64:
            print(f"[vision_worker] Empty image payload for session {session_id}")
            return

        try:
            from PIL import Image
            img_bytes = base64.b64decode(img_b64)
            image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception as e:
            print(f"[vision_worker] Image decode error: {e}")
            return

        # --- Qwen-VL analysis (AI — runs only in worker) ---
        from services.vision_service import vision_service
        event = await vision_service.analyze_frame(image, session_id, timestamp)

        # --- Fusion ---
        from services.fusion_service import fusion_engine
        await fusion_engine.process_video_event(event)

        if event.visual_context:
            visual_event = {
                "event_type": "visual_context",
                "meeting_id": session_id,
                "source":     "vision_worker",
                "timestamp":  now_iso(),
                "content":    event.visual_context,
                "priority":   "low",
                "data": {
                    "visual_context": event.visual_context,
                    "descriptions":   event.descriptions,
                },
            }
            # Publish to Redis Pub/Sub → RedisBroadcaster → WebSocket → Frontend
            await redis_client.publish_scrum_event(visual_event)

        print(f"[vision_worker] session={session_id} "
              f"visual={bool(event.visual_context)}")
