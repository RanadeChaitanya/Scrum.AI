import redis.asyncio as redis
from typing import Optional, List, Dict, Any
import json
from configs.settings import settings


class RedisClient:
    def __init__(self):
        self._client: Optional[redis.Redis] = None
    
    async def connect(self):
        self._client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            decode_responses=True
        )
    
    async def disconnect(self):
        if self._client:
            await self._client.close()
    
    async def publish_event(self, stream: str, event: Dict[str, Any]):
        if not self._client:
            raise RuntimeError("Redis client not connected")
        await self._client.xadd(stream, {"data": json.dumps(event)})
    
    async def read_stream(self, stream: str, last_id: str = "0", count: int = 100):
        if not self._client:
            raise RuntimeError("Redis client not connected")
        return await self._client.xread({stream: last_id}, count=count)
    
    async def set_session_state(self, session_id: str, key: str, value: Any):
        if not self._client:
            raise RuntimeError("Redis client not connected")
        full_key = f"session:{session_id}:{key}"
        await self._client.set(full_key, json.dumps(value))
    
    async def get_session_state(self, session_id: str, key: str) -> Optional[Any]:
        if not self._client:
            raise RuntimeError("Redis client not connected")
        full_key = f"session:{session_id}:{key}"
        value = await self._client.get(full_key)
        if value:
            return json.loads(value)
        return None
    
    async def append_to_session_list(self, session_id: str, list_name: str, item: Any):
        if not self._client:
            raise RuntimeError("Redis client not connected")
        full_key = f"session:{session_id}:{list_name}"
        await self._client.rpush(full_key, json.dumps(item))
    
    async def get_session_list(self, session_id: str, list_name: str) -> List[Any]:
        if not self._client:
            raise RuntimeError("Redis client not connected")
        full_key = f"session:{session_id}:{list_name}"
        items = await self._client.lrange(full_key, 0, -1)
        return [json.loads(item) for item in items]
    
    async def delete_session(self, session_id: str):
        if not self._client:
            raise RuntimeError("Redis client not connected")
        keys = await self._client.keys(f"session:{session_id}:*")
        if keys:
            await self._client.delete(*keys)


redis_client = RedisClient()


async def get_redis() -> RedisClient:
    return redis_client