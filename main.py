from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import uuid
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
from services.websocket_service import connection_manager


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


@app.on_event("startup")
async def startup():
    await redis_client.connect()
    await audio_service.initialize()
    await vision_service.initialize()
    await fusion_engine.initialize()
    await qwen_service.initialize()
    await deepseek_service.initialize()
    print("All services initialized")


@app.on_event("shutdown")
async def shutdown():
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


@app.get("/trello-auth", response_class=HTMLResponse)
async def serve_trello_auth():
    """Trello OAuth connection page."""
    return _read_page("trello-auth.html")


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
    await audio_service.stop_session(request.session_id)
    await vision_service.stop_session(request.session_id)
    await fusion_engine.stop_session(request.session_id)
    await qwen_service.stop_session(request.session_id)
    
    result = {"session_id": request.session_id, "status": "ended"}
    
    if request.generate_report:
        report = await deepseek_service.refine_session(request.session_id)
        await redis_client.set_session_state(request.session_id, "report", report.model_dump())
        result["report"] = report.model_dump()
    
    return result


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
    audio_bytes = await file.read()
    
    import numpy as np
    import io
    import wave
    
    with io.BytesIO(audio_bytes) as wav_file:
        with wave.open(wav_file, 'rb') as wav:
            sample_rate = wav.getframerate()
            n_channels = wav.getnchannels()
            frames = wav.readframes(wav.getnframes())
            audio_array = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    if n_channels > 1:
        audio_array = audio_array.reshape(-1, n_channels).mean(axis=1)
    
    timestamp = 0.0
    event = await audio_service.process_audio_chunk(
        audio_array, session_id, timestamp, sample_rate
    )
    
    fusion_events = await fusion_engine.process_audio_event(event)
    
    for fe in fusion_events:
        update = await qwen_service.analyze_meeting_event(fe)
        if update:
            await connection_manager.broadcast_scrum_update(update, session_id)
    
    return {"session_id": session_id, "fusion_events_count": len(fusion_events)}


@app.post("/video/process")
async def process_video(session_id: str, file: UploadFile = File(...)):
    from PIL import Image
    import io
    
    image_bytes = await file.read()
    image = Image.open(io.BytesIO(image_bytes))
    
    timestamp = 0.0
    event = await vision_service.analyze_frame(image, session_id, timestamp)
    
    fusion_events = await fusion_engine.process_video_event(event)
    
    return {"session_id": session_id, "visual_context": event.visual_context}


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
    await connection_manager.connect(websocket, session_id)
    try:
        while True:
            data = await websocket.receive_text()
            await connection_manager.handle_message(
                {"type": "message", "data": data},
                websocket,
                session_id
            )
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket, session_id)
    except Exception as e:
        print(f"WebSocket error for session {session_id}: {e}")
        connection_manager.disconnect(websocket, session_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)