import redis.asyncio as aioredis
from typing import Optional, List, Dict, Any
import json
import os
from pathlib import Path
from configs.settings import settings

# ---------------------------------------------------------------------------
# JSON fallback store — used when Redis is unavailable
# Stored at: .session_store.json (gitignored, local only)
# ---------------------------------------------------------------------------
_FALLBACK_FILE = Path(__file__).resolve().parent / ".session_store.json"


def _load_fallback() -> Dict[str, Any]:
    if _FALLBACK_FILE.exists():
        try:
            return json.loads(_FALLBACK_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_fallback(data: Dict[str, Any]) -> None:
    _FALLBACK_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


class RedisClient:
    def __init__(self):
        self._client: Optional[aioredis.Redis] = None
        self._redis_ok: bool = False   # True only after a successful ping

    async def connect(self):
        try:
            self._client = aioredis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            await self._client.ping()
            self._redis_ok = True
            print(f"[redis] Connected to {settings.redis_host}:{settings.redis_port}")
        except Exception as e:
            self._redis_ok = False
            print(f"[redis] WARNING: Redis unavailable ({e}). Using JSON fallback store.")

    async def disconnect(self):
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal helpers — transparently switch between Redis and JSON file
    # ------------------------------------------------------------------
    async def _redis_set(self, key: str, value: str) -> None:
        if self._redis_ok and self._client:
            await self._client.set(key, value)
        else:
            store = _load_fallback()
            store[key] = value
            _save_fallback(store)

    async def _redis_get(self, key: str) -> Optional[str]:
        if self._redis_ok and self._client:
            return await self._client.get(key)
        else:
            store = _load_fallback()
            return store.get(key)

    async def _redis_rpush(self, key: str, value: str) -> None:
        if self._redis_ok and self._client:
            await self._client.rpush(key, value)
        else:
            store = _load_fallback()
            lst = json.loads(store.get(key, "[]"))
            lst.append(value)
            store[key] = json.dumps(lst)
            _save_fallback(store)

    async def _redis_lrange(self, key: str) -> List[str]:
        if self._redis_ok and self._client:
            return await self._client.lrange(key, 0, -1)
        else:
            store = _load_fallback()
            raw = store.get(key, "[]")
            return json.loads(raw)

    async def _redis_keys(self, pattern: str) -> List[str]:
        if self._redis_ok and self._client:
            return await self._client.keys(pattern)
        else:
            store = _load_fallback()
            import fnmatch
            return [k for k in store if fnmatch.fnmatch(k, pattern)]

    async def _redis_delete(self, *keys: str) -> None:
        if self._redis_ok and self._client:
            await self._client.delete(*keys)
        else:
            store = _load_fallback()
            for k in keys:
                store.pop(k, None)
            _save_fallback(store)

    # ------------------------------------------------------------------
    # Public API (unchanged interface — callers don't need to change)
    # ------------------------------------------------------------------
    async def publish_event(self, stream: str, event: Dict[str, Any]) -> None:
        if self._redis_ok and self._client:
            await self._client.xadd(stream, {"data": json.dumps(event)})
        else:
            # Append to fallback list under the stream key
            await self._redis_rpush(f"stream:{stream}", json.dumps(event))

    async def read_stream(self, stream: str, last_id: str = "0", count: int = 100):
        if self._redis_ok and self._client:
            return await self._client.xread({stream: last_id}, count=count)
        return []

    async def set_session_state(self, session_id: str, key: str, value: Any) -> None:
        full_key = f"session:{session_id}:{key}"
        await self._redis_set(full_key, json.dumps(value))

    async def get_session_state(self, session_id: str, key: str) -> Optional[Any]:
        full_key = f"session:{session_id}:{key}"
        raw = await self._redis_get(full_key)
        if raw:
            return json.loads(raw)
        return None

    async def append_to_session_list(self, session_id: str, list_name: str, item: Any) -> None:
        full_key = f"session:{session_id}:{list_name}"
        await self._redis_rpush(full_key, json.dumps(item))

    async def get_session_list(self, session_id: str, list_name: str) -> List[Any]:
        full_key = f"session:{session_id}:{list_name}"
        items = await self._redis_lrange(full_key)
        return [json.loads(item) for item in items]

    async def delete_session(self, session_id: str) -> None:
        keys = await self._redis_keys(f"session:{session_id}:*")
        if keys:
            await self._redis_delete(*keys)


redis_client = RedisClient()


async def get_redis() -> RedisClient:
    return redis_client
