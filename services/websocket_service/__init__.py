"""
WebSocket Service
=================
ConnectionManager — tracks FastAPI WebSocket connections by session_id.
RedisBroadcaster  — background task. Subscribes to Redis Pub/Sub channel
                    "scrum_updates" and fans every message to WebSocket clients.

Data flow:
  Worker → redis_client.publish_scrum_event()
    ├─ [Redis up]   → Redis PUBLISH scrum_updates
    │                  → RedisBroadcaster async listener
    └─ [Redis down] → core._get_pubsub_queue() asyncio.Queue
                       → RedisBroadcaster queue reader
  → ConnectionManager.broadcast(meeting_id)
  → FastAPI WebSocket → Browser JS handleLiveEvent()
"""
import asyncio
import json
import traceback
from dataclasses import dataclass
from typing import Dict, Any, Set, Optional

import websockets
from fastapi import WebSocket

from models import LiveUpdate, ScrumUpdate
from configs.settings import settings


# ─────────────────────────────────────────────────────────────────────────────
# ConnectionManager
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Client:
    websocket: WebSocket
    session_id: str


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[Client]] = {}

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        self.active_connections.setdefault(session_id, set())
        self.active_connections[session_id].add(Client(websocket, session_id))
        total = sum(len(v) for v in self.active_connections.values())
        print(f"[ws] Client connected session={session_id} total_clients={total}")

    def disconnect(self, websocket: WebSocket, session_id: str) -> None:
        if session_id in self.active_connections:
            self.active_connections[session_id] = {
                c for c in self.active_connections[session_id]
                if c.websocket != websocket
            }
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
        print(f"[ws] Client disconnected session={session_id}")

    async def broadcast(self, message: Dict[str, Any], session_id: str) -> None:
        clients = self.active_connections.get(session_id, set())
        if not clients:
            return
        disconnected: Set[Client] = set()
        for client in clients:
            try:
                await client.websocket.send_json(message)
            except Exception:
                disconnected.add(client)
        for client in disconnected:
            self.disconnect(client.websocket, session_id)

    async def broadcast_all(self, message: Dict[str, Any]) -> None:
        for session_id in list(self.active_connections.keys()):
            await self.broadcast(message, session_id)

    async def send_personal(self, message: Dict[str, Any], websocket: WebSocket) -> None:
        try:
            await websocket.send_json(message)
        except Exception:
            pass

    async def broadcast_scrum_update(self, scrum: ScrumUpdate, session_id: str) -> None:
        await self.broadcast({"type": "scrum_update", "data": scrum.model_dump()}, session_id)

    async def broadcast_live_update(self, update: LiveUpdate, session_id: str) -> None:
        await self.broadcast({"type": "live_update", "data": update.model_dump()}, session_id)

    def client_count(self) -> int:
        return sum(len(v) for v in self.active_connections.values())


# ─────────────────────────────────────────────────────────────────────────────
# RedisBroadcaster
# ─────────────────────────────────────────────────────────────────────────────

class RedisBroadcaster:
    """
    Single background asyncio.Task that bridges Redis → WebSocket clients.

    Path A (Redis available):
      Uses aioredis async pubsub.listen() — fully non-blocking, no executor.
      One dedicated connection per broadcaster instance.

    Path B (Redis unavailable):
      Reads from core._get_pubsub_queue() — in-process asyncio.Queue.
      All workers fall back to this same queue via publish_scrum_event().
      Latency is <1ms since everything is in the same process.

    Reconnection: on any exception, wait 1s and retry from scratch.
    """

    def __init__(self, manager: ConnectionManager):
        self._manager = manager
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._listen(), name="redis_broadcaster")
        print(f"[broadcaster] Started (channel={settings.scrum_pubsub_channel})")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print("[broadcaster] Stopped")

    async def _listen(self) -> None:
        from core import redis_client, _get_pubsub_queue

        print(f"[broadcaster] Loop started. Redis ok={redis_client._redis_ok}")

        while self._running:
            try:
                if redis_client._redis_ok and redis_client._client:
                    await self._listen_redis(redis_client)
                else:
                    await self._listen_fallback(_get_pubsub_queue())
            except asyncio.CancelledError:
                break
            except Exception:
                print(f"[broadcaster] ERROR — will retry in 1s:\n{traceback.format_exc()}")
                await asyncio.sleep(1.0)

    async def _listen_redis(self, redis_client) -> None:
        """
        Uses aioredis async pubsub.listen() — fully async, no blocking executor.
        Exits (raises) on any error so the outer loop can reconnect.
        """
        import redis.asyncio as aioredis
        ps_conn = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=True,
        )
        pubsub = ps_conn.pubsub()
        try:
            await pubsub.subscribe(settings.scrum_pubsub_channel)
            print(f"[broadcaster] Subscribed to Redis '{settings.scrum_pubsub_channel}'")

            # listen() is an async generator — yields messages as they arrive
            async for msg in pubsub.listen():
                if not self._running:
                    break
                if msg and msg.get("type") == "message":
                    data = msg.get("data", "")
                    print(f"[broadcaster] Redis msg → {self._manager.client_count()} client(s)")
                    await self._dispatch(data)
        finally:
            try:
                await pubsub.unsubscribe(settings.scrum_pubsub_channel)
                await pubsub.aclose()
                await ps_conn.aclose()
            except Exception:
                pass

    async def _listen_fallback(self, queue: asyncio.Queue) -> None:
        """
        Reads from the in-process asyncio.Queue when Redis is unavailable.
        Runs until _running is False or Redis comes back online.
        """
        from core import redis_client
        while self._running:
            # Switch to Redis if it becomes available
            if redis_client._redis_ok and redis_client._client:
                print("[broadcaster] Redis now available — switching from fallback queue")
                return

            try:
                payload = await asyncio.wait_for(queue.get(), timeout=0.2)
                print(f"[broadcaster] Queue msg → {self._manager.client_count()} client(s)")
                await self._dispatch(payload)
            except asyncio.TimeoutError:
                pass  # no message yet, loop again

    async def _dispatch(self, payload: str) -> None:
        """Parse scrum event and fan out to matching WebSocket clients."""
        try:
            event = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as e:
            print(f"[broadcaster] JSON parse error: {e!r} — raw={payload!r:.120}")
            return

        meeting_id = event.get("meeting_id")
        event_type = event.get("event_type", "unknown")
        content    = event.get("content", "")[:60]

        if meeting_id:
            n = len(self._manager.active_connections.get(meeting_id, set()))
            print(f"[broadcaster] → event_type={event_type!r} meeting={meeting_id!r} "
                  f"content={content!r} clients={n}")
            await self._manager.broadcast(event, meeting_id)
        else:
            n = self._manager.client_count()
            print(f"[broadcaster] → broadcast_all event_type={event_type!r} "
                  f"content={content!r} clients={n}")
            await self._manager.broadcast_all(event)


# ─────────────────────────────────────────────────────────────────────────────
# Legacy WebSocketServer (reference only — not used by FastAPI)
# ─────────────────────────────────────────────────────────────────────────────

class WebSocketServer:
    def __init__(self):
        self.manager = ConnectionManager()
        self._server: Optional[websockets.WebSocketServer] = None

    async def start(self):
        self._server = await websockets.serve(
            self.handle_websocket, settings.ws_host, settings.ws_port
        )

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def handle_websocket(self, websocket, path: str):
        session_id = path.lstrip("/")
        await self.manager.connect(websocket, session_id)
        try:
            async for raw in websocket:
                data = json.loads(raw)
                if data.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.manager.disconnect(websocket, session_id)


# ─────────────────────────────────────────────────────────────────────────────
# Singletons
# ─────────────────────────────────────────────────────────────────────────────
connection_manager = ConnectionManager()
redis_broadcaster  = RedisBroadcaster(connection_manager)
