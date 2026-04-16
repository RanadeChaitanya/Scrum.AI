"""
Base worker — consumes jobs from a Redis list (BLPOP) and processes them.
All workers inherit from this. Each worker runs as an asyncio background task.
"""
import asyncio
import json
import traceback
from typing import Any, Dict, Optional
from core import redis_client


class BaseWorker:
    """
    Pulls jobs from `queue_key` (Redis list) using non-blocking LPOP with
    a short sleep, processes each job, and publishes results back to Redis.
    Falls back to in-process queue when Redis is unavailable.
    """

    queue_key: str = ""          # subclass must set this
    worker_name: str = "worker"  # for logging

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._local_queue: asyncio.Queue = asyncio.Queue()  # fallback when Redis down

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop(), name=self.worker_name)
        print(f"[{self.worker_name}] Worker started (queue: {self.queue_key})")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print(f"[{self.worker_name}] Worker stopped")

    # ------------------------------------------------------------------
    # Enqueue (called by HTTP router — never blocks)
    # ------------------------------------------------------------------
    async def enqueue(self, job: Dict[str, Any]) -> None:
        payload = json.dumps(job)
        if redis_client._redis_ok and redis_client._client:
            await redis_client._client.rpush(self.queue_key, payload)
        else:
            await self._local_queue.put(payload)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def _loop(self):
        while self._running:
            try:
                payload = await self._dequeue()
                if payload is None:
                    await asyncio.sleep(0.05)
                    continue
                job = json.loads(payload)
                await self.process(job)
            except asyncio.CancelledError:
                break
            except Exception:
                print(f"[{self.worker_name}] ERROR in job:\n{traceback.format_exc()}")
                await asyncio.sleep(0.1)

    async def _dequeue(self) -> Optional[str]:
        # Try Redis first
        if redis_client._redis_ok and redis_client._client:
            try:
                result = await redis_client._client.lpop(self.queue_key)
                return result  # None if queue empty
            except Exception:
                pass
        # Fallback to in-process queue (non-blocking)
        try:
            return self._local_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    # ------------------------------------------------------------------
    # Subclass must implement
    # ------------------------------------------------------------------
    async def process(self, job: Dict[str, Any]) -> None:
        raise NotImplementedError
