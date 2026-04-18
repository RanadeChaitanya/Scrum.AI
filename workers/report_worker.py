"""
Report Worker — consumes report generation jobs from Redis queue.
Runs: DeepSeek final summarization → publishes structured report_ready event.

Output format:
{
  "event_type":  "report_ready",
  "meeting_id":  "<session_id>",
  "source":      "report_worker",
  "timestamp":   "<utc_iso>",
  "content":     "<summary preview>",
  "priority":    "high",
  "data":        { summary, tasks, decisions, blockers }
}
"""
from datetime import datetime, timezone
from typing import Any, Dict

from workers.base import BaseWorker
from core import redis_client
from utils.time import now_iso


class ReportWorker(BaseWorker):
    queue_key   = "jobs:report"
    worker_name = "report_worker"

    async def process(self, job: Dict[str, Any]) -> None:
        session_id = job["session_id"]

        # --- DeepSeek final summarization (AI — runs only in worker) ---
        from services.reasoning_service import deepseek_service
        report = await deepseek_service.refine_session(session_id)

        # Persist report to Redis event store
        try:
            await redis_client.set_session_state(session_id, "report", report.model_dump())
            await redis_client.set_session_state(session_id, "status", {"status": "report_ready"})
        except Exception as e:
            print(f"[report_worker] Redis store error: {e}")
            return

        # Build standardized scrum event
        summary_preview = (report.summary or "")[:200]
        report_event = {
            "event_type": "report_ready",
            "meeting_id": session_id,
            "source":     "report_worker",
            "timestamp":  now_iso(),
            "content":    summary_preview,
            "priority":   "high",
            "data": {
                "summary":   report.summary,
                "tasks":     report.tasks,
                "decisions": report.decisions,
                "blockers":  report.blockers,
            },
        }

        # Publish to Redis Pub/Sub → RedisBroadcaster → WebSocket → Frontend
        await redis_client.publish_scrum_event(report_event)

        print(f"[report_worker] session={session_id} report published")
