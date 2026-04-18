"""
Reasoning Worker — consumes fusion events from Redis queue.
Runs: Qwen 2.5 live reasoning → ScrumUpdate → publishes structured scrum event.

Output format (published to Redis Pub/Sub scrum_updates channel):
{
  "event_type":  "scrum_update",
  "meeting_id":  "<session_id>",
  "source":      "reasoning_worker",
  "timestamp":   "<utc_iso>",
  "content":     "<task description>",
  "priority":    "low | medium | high",
  "data":        { full ScrumUpdate fields }
}
"""
from datetime import datetime, timezone
from typing import Any, Dict

from workers.base import BaseWorker
from core import redis_client
from configs.settings import settings
from utils.time import now_iso


class ReasoningWorker(BaseWorker):
    queue_key   = "jobs:reasoning"
    worker_name = "reasoning_worker"

    async def process(self, job: Dict[str, Any]) -> None:
        session_id  = job["session_id"]
        fusion_data = job.get("fusion_event", {})

        if not fusion_data:
            return

        from models import FusionEvent
        try:
            fusion_event = FusionEvent(**fusion_data)
        except Exception as e:
            print(f"[reasoning_worker] Invalid fusion event: {e}")
            return

        # --- Qwen 2.5 live reasoning (AI — runs only in worker) ---
        from services.reasoning_service import qwen_service
        update = await qwen_service.analyze_meeting_event(fusion_event)

        if not update:
            return

        # Persist structured event to Redis event store
        try:
            await redis_client.append_to_session_list(
                session_id, "scrum_updates", update.model_dump()
            )
        except Exception as e:
            print(f"[reasoning_worker] Redis store error: {e}")

        # Build standardized scrum event
        scrum_event = {
            "event_type": "scrum_update",
            "meeting_id": session_id,
            "source":     "reasoning_worker",
            "timestamp":  now_iso(),
            "content":    update.task or update.blocker or update.decision or "",
            "priority":   update.priority or "medium",
            "data":       update.model_dump(),
        }

        # Publish to Redis Pub/Sub → RedisBroadcaster → WebSocket → Frontend
        await redis_client.publish_scrum_event(scrum_event)

        print(f"[reasoning_worker] session={session_id} task={update.task!r} "
              f"priority={update.priority}")
