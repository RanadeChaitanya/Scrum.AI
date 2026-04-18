"""
Redis health check and pipeline validation script.
Run: python -B scripts/check_redis.py
"""
import asyncio
import json
import time
import sys
import redis.asyncio as aioredis

HOST = "localhost"
PORT = 6379
DB   = 0
CHANNEL = "scrum_updates"
RESULTS = []


def log(status: str, msg: str):
    icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "ℹ️"
    line = f"{icon} [{status}] {msg}"
    print(line)
    RESULTS.append((status, msg))


async def run_checks():
    # ─── 1. Connection health ─────────────────────────────────────────────
    print("\n═══ STEP 1: Redis Connection ═══")
    client = aioredis.Redis(
        host=HOST, port=PORT, db=DB,
        decode_responses=True,
        socket_connect_timeout=2,
    )
    try:
        pong = await client.ping()
        log("PASS", f"Redis ping OK: {pong}")
    except Exception as e:
        log("FAIL", f"Redis not reachable: {e}")
        log("INFO", "Redis is NOT running. All tests will use the in-process fallback queue.")
        log("INFO", "To install Redis on Windows: https://github.com/microsoftarchive/redis/releases")
        await client.aclose()
        return False

    # Server info
    info = await client.info("server")
    log("PASS", f"Redis version: {info.get('redis_version')} mode={info.get('redis_mode','standalone')}")

    # ─── 2. Pub/Sub round-trip ───────────────────────────────────────────
    print("\n═══ STEP 2: Pub/Sub Round-Trip ═══")
    received = []
    
    async def subscriber():
        ps = client.pubsub()
        await ps.subscribe(CHANNEL)
        # Wait up to 2s for a message
        deadline = time.time() + 2.0
        while time.time() < deadline:
            msg = ps.get_message(ignore_subscribe_messages=True)
            if msg and msg.get("type") == "message":
                received.append(msg["data"])
                break
            await asyncio.sleep(0.05)
        await ps.unsubscribe(CHANNEL)
        await ps.aclose()

    test_event = json.dumps({
        "event_type": "scrum_update",
        "meeting_id": "redis-test-session",
        "source":     "redis_test",
        "timestamp":  "2025-01-01T00:00:00Z",
        "content":    "Redis pipeline test event",
        "priority":   "high",
    })

    sub_task = asyncio.create_task(subscriber())
    await asyncio.sleep(0.1)  # let subscriber connect first

    count = await client.publish(CHANNEL, test_event)
    log("PASS", f"Published to '{CHANNEL}' — {count} subscriber(s) received")

    await sub_task

    if received:
        payload = json.loads(received[0])
        log("PASS", f"Pub/Sub received: event_type={payload.get('event_type')!r} content={payload.get('content')!r}")
    else:
        log("FAIL", "Pub/Sub: no message received within 2s")

    # ─── 3. Redis List (job queue) ───────────────────────────────────────
    print("\n═══ STEP 3: Job Queue (Redis List) ═══")
    test_job = json.dumps({"job_id": "test-001", "session_id": "redis-test"})
    await client.rpush("jobs:test_queue", test_job)
    result = await client.lpop("jobs:test_queue")
    if result == test_job:
        log("PASS", "Job queue RPUSH/LPOP round-trip OK")
    else:
        log("FAIL", f"Job queue mismatch: expected {test_job!r} got {result!r}")

    # ─── 4. Redis Stream (event store) ──────────────────────────────────
    print("\n═══ STEP 4: Redis Streams (Event Store) ═══")
    stream_key = "test:event_stream"
    await client.xadd(stream_key, {"data": test_event})
    entries = await client.xread({stream_key: "0"}, count=1)
    if entries:
        log("PASS", f"Redis Streams XADD/XREAD OK — {len(entries[0][1])} entry/entries")
    else:
        log("FAIL", "Redis Streams: no entry read back")
    await client.delete(stream_key)

    # ─── 5. Session state (SET/GET) ──────────────────────────────────────
    print("\n═══ STEP 5: Session State (SET/GET) ═══")
    await client.set("session:test:info", json.dumps({"status": "active"}))
    val = await client.get("session:test:info")
    if val:
        log("PASS", f"SET/GET OK: {json.loads(val)}")
    else:
        log("FAIL", "Session state GET returned None")
    await client.delete("session:test:info")

    # ─── 6. Data integrity — no raw audio stored ─────────────────────────
    print("\n═══ STEP 6: Data Integrity Check ═══")
    keys = await client.keys("session:*")
    audio_keys = [k for k in keys if "audio_raw" in k or "wav" in k or "pcm" in k]
    if audio_keys:
        log("FAIL", f"Raw audio/video found in Redis: {audio_keys}")
    else:
        log("PASS", "No raw audio/video stored in Redis")

    await client.aclose()
    return True


async def test_fallback_queue():
    """Test the in-process asyncio.Queue fallback when Redis is down."""
    print("\n═══ STEP 7: In-Process Fallback Queue ═══")
    sys.path.insert(0, ".")
    from core import _get_pubsub_queue
    q = _get_pubsub_queue()
    test_payload = json.dumps({"event_type": "scrum_update", "meeting_id": "fallback-test"})
    await q.put(test_payload)
    result = await asyncio.wait_for(q.get(), timeout=1.0)
    if result == test_payload:
        log("PASS", "In-process fallback queue PUT/GET OK")
    else:
        log("FAIL", f"Fallback queue mismatch: {result!r}")


async def main():
    print("╔══════════════════════════════════════════════════╗")
    print("║     Redis Pipeline Validation — ScrumAI          ║")
    print("╚══════════════════════════════════════════════════╝")

    redis_ok = await run_checks()
    await test_fallback_queue()

    print("\n╔══════════════════════════════════════════════════╗")
    print("║  SUMMARY                                          ║")
    print("╚══════════════════════════════════════════════════╝")
    passed = sum(1 for s, _ in RESULTS if s == "PASS")
    failed = sum(1 for s, _ in RESULTS if s == "FAIL")
    for status, msg in RESULTS:
        icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "ℹ️"
        print(f"  {icon} {msg}")
    print(f"\n  Total: {passed} passed, {failed} failed")

    if not redis_ok:
        print("\n  ⚠️  Redis is NOT running.")
        print("  The system will use the in-process fallback queue.")
        print("  Events WILL reach WebSocket clients in the same process.")
        print("  To enable full Redis support, install and start Redis:")
        print("    Windows: https://github.com/microsoftarchive/redis/releases")
        print("    Or via WSL: sudo apt install redis-server && redis-server")

    return failed


if __name__ == "__main__":
    failures = asyncio.run(main())
    sys.exit(0 if failures == 0 else 1)
