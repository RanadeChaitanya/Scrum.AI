from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
<<<<<<< HEAD
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
from typing import Optional, List
import uuid
import traceback
=======
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import uuid
import base64
import json
import numpy as np
import io
import wave
>>>>>>> 1aa40e70967db9df875ddd249b538faf7eb4daca
from datetime import datetime
from pathlib import Path

from configs.settings import settings
from core import redis_client
from models import ScrumUpdate, LiveUpdate
from services.audio_service import audio_service
from services.vision_service import vision_service
from services.fusion_service import fusion_engine
from services.reasoning_service import qwen_service, deepseek_service
from services.trello_service import trello_service
from services.websocket_service import connection_manager, redis_broadcaster

# Worker singletons — all AI inference runs here, never in request path
from workers.audio_worker    import AudioWorker
from workers.vision_worker   import VisionWorker
from workers.reasoning_worker import ReasoningWorker
from workers.report_worker   import ReportWorker

audio_worker     = AudioWorker()
vision_worker    = VisionWorker()
reasoning_worker = ReasoningWorker()
report_worker    = ReportWorker()


app = FastAPI(title="AI Scrum Automation System", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------
# Resolve paths relative to this file so the server works regardless of the
# working directory it is launched from.
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
PAGES_DIR    = FRONTEND_DIR / "pages"
ASSETS_DIR   = FRONTEND_DIR / "assets"

# Serve /assets/* (screenshots, design-system docs, etc.)
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


class MeetingStartRequest(BaseModel):
    session_name: Optional[str] = None


class MeetingEndRequest(BaseModel):
    session_id: str
    generate_report: bool = True


class TrelloSyncRequest(BaseModel):
    session_id: str
    list_id: str


class SaveTokenRequest(BaseModel):
    token: str


class TestEventRequest(BaseModel):
    meeting_id: str
    event_type: str = "scrum_update"
    content: str = "Test Scrum Update from debug endpoint"
    priority: str = "medium"
    source: str = "test_worker"


@app.on_event("startup")
async def startup():
    await redis_client.connect()
    # Initialize service connections (no ML loaded here — workers handle that lazily)
    await audio_service.initialize()
    await vision_service.initialize()
    await fusion_engine.initialize()
    await qwen_service.initialize()
    await deepseek_service.initialize()
    # Start Redis→WebSocket broadcaster BEFORE workers so no events are dropped
    await redis_broadcaster.start()
    # Start all workers as background asyncio tasks
    await audio_worker.start()
    await vision_worker.start()
    await reasoning_worker.start()
    await report_worker.start()
    print("All services, broadcaster, and workers initialized")


@app.on_event("shutdown")
async def shutdown():
    # Stop broadcaster and workers gracefully before disconnecting
    await redis_broadcaster.stop()
    await audio_worker.stop()
    await vision_worker.stop()
    await reasoning_worker.stop()
    await report_worker.stop()
    await redis_client.disconnect()
    await trello_service.close()


# ---------------------------------------------------------------------------
# Page helper
# ---------------------------------------------------------------------------
def _read_page(filename: str) -> str:
    """Read an HTML page from the pages directory."""
    page_path = PAGES_DIR / filename
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{filename}' not found")
    return page_path.read_text(encoding="utf-8")


def _read_404() -> str:
    """Read the 404 page. Falls back to a minimal inline response if the file
    is somehow missing so the error handler itself never crashes."""
    page_path = PAGES_DIR / "404.html"
    if page_path.exists():
        return page_path.read_text(encoding="utf-8")
    # Bare-bones fallback — should never be reached in normal operation
    return (
        "<!DOCTYPE html><html><head><title>404 - Not Found</title></head>"
        "<body style='font-family:sans-serif;text-align:center;padding:4rem'>"
        "<h1>404</h1><p>Page not found.</p>"
        "<a href='/dashboard'>Go to Dashboard</a></body></html>"
    )


# ---------------------------------------------------------------------------
# Global exception handlers
# ---------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def custom_404_handler(request: Request, exc: StarletteHTTPException):
    """
    Catches every HTTP error raised anywhere in the app.
    - 404  → serve the custom 404 page with status 404
    - 4xx  → serve the custom 404 page with the original status code
    - 5xx  → serve the custom 404 page with status 500
    API routes (paths starting with /api, /meeting, /audio, /video,
    /trello/sync, /trello/lists, /ws) return JSON errors as before.
    """
    api_prefixes = ("/api", "/meeting", "/audio", "/video",
                    "/trello/sync", "/trello/lists", "/ws")
    if request.url.path.startswith(api_prefixes):
        # Let FastAPI's default JSON handler deal with API errors
        return await http_exception_handler(request, exc)

    return HTMLResponse(
        content=_read_404(),
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def global_runtime_error_handler(request: Request, exc: Exception):
    """
    Catches unhandled runtime exceptions on page routes and returns the
    404 page with a 500 status so the browser still shows something useful.
    API routes surface a JSON error instead.
    """
    api_prefixes = ("/api", "/meeting", "/audio", "/video",
                    "/trello/sync", "/trello/lists", "/ws")
    if request.url.path.startswith(api_prefixes):
        tb = traceback.format_exc()
        print(f"[ERROR] Unhandled exception on {request.url.path}:\n{tb}")
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "path": request.url.path},
        )

    tb = traceback.format_exc()
    print(f"[ERROR] Unhandled exception on {request.url.path}:\n{tb}")
    return HTMLResponse(content=_read_404(), status_code=500)


# ---------------------------------------------------------------------------
# Frontend page routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_landing():
    """Landing page — entry point of the application."""
    return _read_page("landing.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    """Main dashboard with the live Kanban board."""
    return _read_page("dashboard.html")


@app.get("/history", response_class=HTMLResponse)
async def serve_history():
    """Report history and download page."""
    return _read_page("history.html")


@app.get("/profile", response_class=HTMLResponse)
async def serve_profile():
    """User profile and settings page."""
    return _read_page("profile.html")


@app.get("/scrum-report", response_class=HTMLResponse)
async def serve_scrum_report():
    """Final sprint report page."""
    return _read_page("scrum-report.html")


@app.get("/live-test", response_class=HTMLResponse)
async def serve_live_test():
    """End-to-end pipeline test page."""
    return _read_page("live-test.html")


@app.get("/trello-auth", response_class=HTMLResponse)
async def serve_trello_auth():
    """Trello OAuth connection page."""
    return _read_page("trello-auth.html")


@app.get("/404", response_class=HTMLResponse)
async def serve_404():
    """Explicit 404 page — also reachable as a direct URL."""
    return HTMLResponse(content=_read_404(), status_code=404)


# ---------------------------------------------------------------------------
# Trello OAuth flow
# ---------------------------------------------------------------------------
@app.get("/trello/connect")
async def trello_connect():
    """
    Redirect the browser to Trello's OAuth authorisation page.
    The user lands here after clicking 'Log in with Trello' on /trello-auth.
    """
    if not settings.trello_api_key:
        print("[trello_auth] ERROR: TRELLO_API_KEY not configured")
        return RedirectResponse(url=f"{settings.trello_fallback_url}?reason=missing_api_key")

    callback_url = f"{settings.app_base_url}/trello/callback"
    trello_auth_url = (
        f"https://trello.com/1/authorize"
        f"?expiration=never"
        f"&name=ScrumAI"
        f"&scope=read,write"
        f"&response_type=token"
        f"&key={settings.trello_api_key}"
        f"&return_url={callback_url}"
    )
    return RedirectResponse(url=trello_auth_url)


@app.get("/trello/callback", response_class=HTMLResponse)
async def trello_callback():
    """
    Trello redirects here after OAuth. The token is in the URL FRAGMENT (#token=...),
    which browsers never send to the server. This page runs JavaScript to:
      1. Extract the token from window.location.hash
      2. POST it to /save-token on the backend
      3. Show a loading spinner while this happens
    The token is NEVER stored in localStorage or sessionStorage.
    """
    fallback = settings.trello_fallback_url
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Connecting Trello — ScrumAI</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-gray-50 flex items-center justify-center font-sans">
  <div class="bg-white rounded-xl shadow-md p-10 max-w-sm w-full text-center">
    <div id="loading">
      <div class="animate-spin text-5xl mb-4">⚙️</div>
      <h1 class="text-xl font-bold text-gray-800 mb-2">Connecting to Trello...</h1>
      <p class="text-gray-400 text-sm">Please wait while we verify your account.</p>
    </div>
    <div id="error" class="hidden">
      <div class="text-5xl mb-4">⚠️</div>
      <h1 class="text-xl font-bold text-gray-800 mb-2">Connection Failed</h1>
      <p id="error-msg" class="text-gray-500 text-sm mb-6"></p>
      <a href="/trello-auth"
         class="inline-block bg-teal-600 text-white font-medium px-6 py-3 rounded-lg hover:bg-teal-700 transition-colors">
        Try Again
      </a>
    </div>
  </div>

  <script>
    (async () => {{
      // Step 1 — extract token from URL fragment (#token=...)
      // Fragments are never sent to the server, so JS must read them here.
      const hash = window.location.hash;
      const params = new URLSearchParams(hash.replace(/^#/, ''));
      const token = params.get('token');

      if (!token) {{
        showError('No token was returned by Trello. Please try again.');
        setTimeout(() => window.location.href = '{fallback}?reason=no_token', 3000);
        return;
      }}

      // Step 2 — POST token to backend for validation and secure storage.
      // Token is sent once over HTTPS and then discarded from the frontend.
      try {{
        const res = await fetch('/save-token', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ token }})
        }});

        const data = await res.json();

        if (res.ok && data.redirect) {{
          // Step 3 — backend validated and stored the token; follow its redirect
          window.location.href = data.redirect;
        }} else {{
          showError(data.detail || 'Authentication failed. Please try again.');
          setTimeout(() => window.location.href = '{fallback}?reason=invalid_token', 3000);
        }}
      }} catch (e) {{
        showError('Network error. Please check your connection and try again.');
        setTimeout(() => window.location.href = '{fallback}?reason=network_error', 3000);
      }}
    }})();

    function showError(msg) {{
      document.getElementById('loading').classList.add('hidden');
      document.getElementById('error').classList.remove('hidden');
      document.getElementById('error-msg').textContent = msg;
    }}
  </script>
</body>
</html>""")


@app.post("/save-token")
async def save_token(body: SaveTokenRequest):
    """
    Receives the Trello token POSTed by the /trello/callback frontend page.
    1. Validates the token against Trello /1/members/me
    2. Stores token + profile in Redis (backend only — never returned to frontend)
    3. Returns a redirect URL on success, or an error detail on failure
    Token is NEVER stored in frontend localStorage/sessionStorage.
    """
    token = body.token.strip()
    fallback = settings.trello_fallback_url

    if not token:
        print("[trello_auth] ERROR: Empty token received in /save-token")
        return JSONResponse(status_code=400, content={"detail": "Token is empty."})

    # Validate token against Trello API
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.trello.com/1/members/me",
                params={
                    "key":    settings.trello_api_key,
                    "token":  token,
                    "fields": "fullName,username,avatarUrl",
                },
            )

        if resp.status_code != 200:
            print(f"[trello_auth] ERROR: Token validation failed — HTTP {resp.status_code}: {resp.text}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Token rejected by Trello. It may be invalid or expired."}
            )

        profile = resp.json()

    except Exception as e:
        print(f"[trello_auth] ERROR: Trello API call failed — {e}")
        return JSONResponse(
            status_code=502,
            content={"detail": "Could not reach Trello to validate your token."}
        )

    # Store token + profile in Redis — token never leaves the backend after this point
    try:
        await redis_client.set_session_state("trello", "token",   {"token": token})
        await redis_client.set_session_state("trello", "profile", {
            "fullName":  profile.get("fullName",  ""),
            "username":  profile.get("username",  ""),
            "avatarUrl": profile.get("avatarUrl", ""),
        })
        print(f"[trello_auth] Authenticated: @{profile.get('username')} ({profile.get('fullName')})")
    except Exception as e:
        print(f"[trello_auth] ERROR: Failed to store session in Redis — {e}")
        return JSONResponse(
            status_code=500,
            content={"detail": "Token was valid but could not be saved. Please retry."}
        )

    return JSONResponse(status_code=200, content={"redirect": "/dashboard?trello=connected"})


# ---------------------------------------------------------------------------
# Auth error page
# ---------------------------------------------------------------------------
@app.get("/auth/error", response_class=HTMLResponse)
async def auth_error(reason: Optional[str] = None):
    """Fallback page shown on any OAuth failure."""
    messages = {
        "missing_api_key": "Trello API key is not configured on the server.",
        "no_token":        "No token was returned by Trello. Please try again.",
        "invalid_token":   "The Trello token is invalid or has expired.",
        "validation_error":"Could not reach Trello to validate your token.",
        "session_error":   "Token was valid but could not be saved. Please retry.",
    }
    msg = messages.get(reason or "", "An unknown authentication error occurred.")
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Authentication Error — ScrumAI</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-gray-50 flex items-center justify-center font-sans">
  <div class="bg-white rounded-xl shadow-md p-10 max-w-md w-full text-center">
    <div class="text-5xl mb-4">⚠️</div>
    <h1 class="text-2xl font-bold text-gray-800 mb-2">Authentication Failed</h1>
    <p class="text-gray-500 text-sm mb-6">{msg}</p>
    <p class="text-xs text-gray-400 mb-8">Error code: <code class="bg-gray-100 px-1 rounded">{reason or "unknown"}</code></p>
    <a href="/trello-auth"
       class="inline-block bg-teal-600 text-white font-medium px-6 py-3 rounded-lg hover:bg-teal-700 transition-colors">
      Try Again
    </a>
    <a href="/"
       class="inline-block ml-3 text-gray-500 text-sm underline hover:text-gray-700">
      Go Home
    </a>
  </div>
</body>
</html>""", status_code=200)


# ---------------------------------------------------------------------------
# Trello user profile endpoint (secure — token never leaves backend)
# ---------------------------------------------------------------------------
@app.get("/api/user/profile")
async def get_user_profile():
    """
    Returns the authenticated Trello user's profile.
    Token is read from Redis (backend only) — never exposed to frontend.
    """
    try:
        profile = await redis_client.get_session_state("trello", "profile")
        if not profile:
            raise HTTPException(status_code=401, detail="Not authenticated with Trello")
        return {
            "fullName":  profile.get("fullName", ""),
            "username":  profile.get("username", ""),
            "avatarUrl": profile.get("avatarUrl", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[trello_auth] ERROR: Could not fetch profile — {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve profile")


# ---------------------------------------------------------------------------
# Health / API info
# ---------------------------------------------------------------------------
@app.get("/api")
async def root():
    return {"message": "AI Scrum Automation System API", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Debug / test endpoints
# ---------------------------------------------------------------------------

@app.post("/debug/emit")
async def debug_emit_event(req: TestEventRequest):
    """
    Inject a test event directly into the publish_scrum_event pipeline.
    Use this to verify the full Worker→Redis→WebSocket→Frontend flow.

    Example:
      curl -X POST http://localhost:8000/debug/emit \\
           -H 'Content-Type: application/json' \\
           -d '{"meeting_id":"test-123","content":"Fix login bug","priority":"high"}'
    """
    from datetime import datetime, timezone
    from utils.time import now_iso
    event = {
        "event_type": req.event_type,
        "meeting_id": req.meeting_id,
        "source":     req.source,
        "timestamp":  now_iso(),
        "content":    req.content,
        "priority":   req.priority,
        "data": {
            "task":     req.content,
            "status":   "todo",
            "owner":    None,
            "priority": req.priority,
            "timestamp": datetime.now(timezone.utc).timestamp(),
        },
    }
    await redis_client.publish_scrum_event(event)
    clients = connection_manager.client_count()
    print(f"[debug] Emitted test event meeting_id={req.meeting_id!r} "
          f"content={req.content!r} ws_clients={clients}")
    return {"ok": True, "event": event, "ws_clients": clients}


@app.get("/debug/status")
async def debug_status():
    """Show current system state: Redis, broadcaster, connected clients."""
    return {
        "redis_ok":      redis_client._redis_ok,
        "broadcaster":   "running" if redis_broadcaster._task and not redis_broadcaster._task.done() else "stopped",
        "ws_clients":    connection_manager.client_count(),
        "ws_sessions":   list(connection_manager.active_connections.keys()),
        "workers": {
            "audio":     "running" if audio_worker._task and not audio_worker._task.done() else "stopped",
            "vision":    "running" if vision_worker._task and not vision_worker._task.done() else "stopped",
            "reasoning": "running" if reasoning_worker._task and not reasoning_worker._task.done() else "stopped",
            "report":    "running" if report_worker._task and not report_worker._task.done() else "stopped",
        },
    }


@app.post("/meeting/start")
async def start_meeting(request: MeetingStartRequest):
    session_id = str(uuid.uuid4())
    
    await audio_service.start_session(session_id)
    await vision_service.start_session(session_id)
    await fusion_engine.start_session(session_id)
    await qwen_service.start_session(session_id)
    
    session_data = {
        "session_id": session_id,
        "session_name": request.session_name,
        "started_at": datetime.now().isoformat(),
        "status": "active"
    }
    await redis_client.set_session_state(session_id, "info", session_data)
    
    return {"session_id": session_id, "status": "active"}


@app.post("/meeting/end")
async def end_meeting(request: MeetingEndRequest):
    """
    ROUTER ONLY — stops session services, enqueues report job if requested.
    Returns immediately. DeepSeek runs in report_worker.
    """
    await audio_service.stop_session(request.session_id)
    await vision_service.stop_session(request.session_id)
    await fusion_engine.stop_session(request.session_id)
    await qwen_service.stop_session(request.session_id)

    await redis_client.set_session_state(request.session_id, "status", {"status": "ended"})

    if request.generate_report:
        await report_worker.enqueue({"session_id": request.session_id})
        return {
            "session_id": request.session_id,
            "status":     "ended",
            "report":     "generating",
            "message":    "Report is being generated. Listen on WebSocket for report_ready event.",
        }

    return {"session_id": request.session_id, "status": "ended"}


@app.get("/meeting/{session_id}")
async def get_meeting(session_id: str):
    session_info = await redis_client.get_session_state(session_id, "info")
    fusion_events = await redis_client.get_session_list(session_id, "fusion_events")
    scrum_updates = await redis_client.get_session_list(session_id, "scrum_updates")
    report = await redis_client.get_session_state(session_id, "report")
    
    return {
        "session_id": session_id,
        "session_info": session_info,
        "fusion_events_count": len(fusion_events),
        "scrum_updates_count": len(scrum_updates),
        "report": report
    }


@app.post("/audio/process")
async def process_audio(session_id: str, file: UploadFile = File(...)):
    """
    INGESTION ONLY — validates input, converts to float32, enqueues job.
    Returns immediately with job_id. All AI runs in audio_worker.
    """
    audio_bytes = await file.read()

    with io.BytesIO(audio_bytes) as wav_file:
        with wave.open(wav_file, "rb") as wav:
            sample_rate = wav.getframerate()
            n_channels  = wav.getnchannels()
            frames      = wav.readframes(wav.getnframes())
            audio_array = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    if n_channels > 1:
        audio_array = audio_array.reshape(-1, n_channels).mean(axis=1)

    # Encode as base64 for JSON-safe transport through Redis queue
    audio_b64 = base64.b64encode(audio_array.astype(np.float32).tobytes()).decode()

    job_id = str(uuid.uuid4())
    await audio_worker.enqueue({
        "job_id":      job_id,
        "session_id":  session_id,
        "audio_b64":   audio_b64,
        "sample_rate": sample_rate,
        "timestamp":   0.0,
    })

    return {"session_id": session_id, "job_id": job_id, "status": "queued"}


@app.post("/video/process")
async def process_video(session_id: str, file: UploadFile = File(...)):
    """
    INGESTION ONLY — validates input, enqueues job.
    Returns immediately. All AI runs in vision_worker.
    """
    image_bytes = await file.read()
    image_b64   = base64.b64encode(image_bytes).decode()

    job_id = str(uuid.uuid4())
    await vision_worker.enqueue({
        "job_id":     job_id,
        "session_id": session_id,
        "image_b64":  image_b64,
        "timestamp":  0.0,
    })

    return {"session_id": session_id, "job_id": job_id, "status": "queued"}


@app.post("/trello/sync")
async def sync_to_trello(request: TrelloSyncRequest):
    scrum_updates_data = await redis_client.get_session_list(request.session_id, "scrum_updates")
    
    results = []
    for update_data in scrum_updates_data:
        scrum = ScrumUpdate(**update_data)
        if scrum.task:
            card = await trello_service.create_card_from_scrum(scrum, request.list_id)
            results.append({"task": scrum.task, "card_id": card.get("id") if card else None})
    
    return {"session_id": request.session_id, "cards_created": len(results), "results": results}


@app.get("/trello/lists")
async def get_trello_lists():
    lists = await trello_service.get_board_lists()
    return {"lists": lists}


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    INGESTION + OUTPUT layer.
    - Receives: audio chunks, video frames, control messages
    - Sends: live scrum updates, task assignments, report_ready events
    - MUST NOT run any ML — dispatches jobs to workers via Redis queue
    """
    await connection_manager.connect(websocket, session_id)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")

            # ── Ping / subscribe (control messages) ──────────────────────
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "subscribe":
                await websocket.send_json({"type": "subscribed", "session_id": session_id})

            # ── Audio chunk ingestion ─────────────────────────────────────
            elif msg_type == "audio_chunk":
                # Expects: { type, audio_b64, sample_rate, timestamp }
                job_id = str(uuid.uuid4())
                await audio_worker.enqueue({
                    "job_id":      job_id,
                    "session_id":  session_id,
                    "audio_b64":   msg.get("audio_b64", ""),
                    "sample_rate": msg.get("sample_rate", 16000),
                    "timestamp":   msg.get("timestamp", 0.0),
                })
                # Immediate ack — no blocking
                await websocket.send_json({"type": "ack", "job_id": job_id})

            # ── Video frame ingestion ─────────────────────────────────────
            elif msg_type == "video_frame":
                # Expects: { type, image_b64, timestamp }
                job_id = str(uuid.uuid4())
                await vision_worker.enqueue({
                    "job_id":     job_id,
                    "session_id": session_id,
                    "image_b64":  msg.get("image_b64", ""),
                    "timestamp":  msg.get("timestamp", 0.0),
                })
                await websocket.send_json({"type": "ack", "job_id": job_id})

            else:
                await websocket.send_json({"type": "error", "detail": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        connection_manager.disconnect(websocket, session_id)
    except Exception as e:
        print(f"[ws] Error for session {session_id}: {e}")
        connection_manager.disconnect(websocket, session_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)