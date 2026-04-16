"""
Audio Worker — consumes audio jobs from Redis queue.
Runs: Pyannote diarization → NVIDIA ASR → Fusion → enqueues reasoning jobs.
Publishes a structured transcript_ready event to Redis Pub/Sub after processing.

Output event format:
{
  "event_type":  "transcript_ready",
  "meeting_id":  "<session_id>",
  "source":      "audio_worker",
  "timestamp":   "<utc_iso>",
  "content":     "<combined transcript text>",
  "priority":    "medium",
  "data":        { speaker_segments, transcripts, duration }
}
"""
from datetime import datetime, timezone
from typing import Any, Dict
import base64

import numpy as np

from workers.base import BaseWorker
from core import redis_client
from configs.settings import settings
from utils.time import now_iso


class AudioWorker(BaseWorker):
    queue_key   = "jobs:audio"
    worker_name = "audio_worker"

    async def process(self, job: Dict[str, Any]) -> None:
        session_id  = job["session_id"]
        sample_rate = job.get("sample_rate", 16000)
        timestamp   = job.get("timestamp", 0.0)

        raw_b64 = job.get("audio_b64", "")
        if not raw_b64:
            print(f"[audio_worker] Empty audio payload for session {session_id}")
            return

        pcm_bytes   = base64.b64decode(raw_b64)
        audio_array = np.frombuffer(pcm_bytes, dtype=np.float32)

        # --- Diarization (Pyannote) ---
        from services.audio_service import audio_service
        speaker_segments = await audio_service.diarization.diarize(audio_array, sample_rate)

        # --- ASR (NVIDIA Parakeet) ---
        transcripts = await audio_service.asr.transcribe(audio_array, sample_rate, speaker_segments)

        # --- Build AudioEvent ---
        from models import AudioEvent
        event = AudioEvent(
            session_id       = session_id,
            timestamp        = timestamp,
            sample_rate      = sample_rate,
            duration         = len(audio_array) / sample_rate,
            speaker_segments = speaker_segments,
            transcripts      = transcripts,
        )

        # Persist audio event to Redis stream
        try:
            await redis_client.publish_event(settings.audio_event_stream, event.model_dump())
        except Exception as e:
            print(f"[audio_worker] Redis publish error: {e}")

        # --- Fusion ---
        from services.fusion_service import fusion_engine
        fusion_events = await fusion_engine.process_audio_event(event)

        # --- Enqueue reasoning job for each fusion event with text ---
        # Use BaseWorker.enqueue directly to avoid circular import from main
        from workers.base import BaseWorker as _BW
        import json as _json

        for fe in fusion_events:
            if fe.text:
                job_payload = _json.dumps({
                    "session_id":   session_id,
                    "fusion_event": fe.model_dump(),
                })
                # Push directly to the reasoning queue key
                if redis_client._redis_ok and redis_client._client:
                    await redis_client._client.rpush("jobs:reasoning", job_payload)
                else:
                    # In-process fallback: find the reasoning worker
                    try:
                        import main as _m
                        await _m.reasoning_worker._local_queue.put(job_payload)
                    except Exception:
                        pass
            if fe.text:
                await reasoning_worker.enqueue({
                    "session_id":   session_id,
                    "fusion_event": fe.model_dump(),
                })

        # Build combined transcript text for the live event
        combined_text = " ".join(t.text for t in transcripts if t.text).strip()
        speaker_label = transcripts[0].speaker if transcripts else "Unknown"

        if combined_text:
            transcript_event = {
                "event_type": "transcript_ready",
                "meeting_id": session_id,
                "source":     "audio_worker",
                "timestamp":  now_iso(),
                "content":    f"{speaker_label}: {combined_text}",
                "priority":   "medium",
                "data": {
                    "speaker_segments": [s.model_dump() for s in speaker_segments],
                    "transcripts":      [t.model_dump() for t in transcripts],
                    "duration":         event.duration,
                },
            }
            await redis_client.publish_scrum_event(transcript_event)

        print(f"[audio_worker] session={session_id} "
              f"speakers={len(speaker_segments)} transcripts={len(transcripts)} "
              f"fusion={len(fusion_events)}")
