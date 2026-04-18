from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel
from typing import Optional, List
import uuid
import traceback
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
        raise HTTPException(
            status_code=503,
            detail="Trello API key not configured. Set TRELLO_API_KEY in .env"
        )
    callback_url = "http://localhost:8000/trello/callback"
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


@app.get("/trello/callback")
async def trello_callback(token: Optional[str] = None, request: Request = None):
    """
    Trello redirects back here with the OAuth token as a query parameter.
    In production this token should be stored server-side (e.g. in Redis or a DB).
    For now we redirect to the dashboard with a success flag.
    """
    if not token:
        return RedirectResponse(url="/trello-auth?error=no_token")
    # Store token in Redis so the session can use it
    await redis_client.set_session_state("trello", "token", {"token": token})
    return RedirectResponse(url="/dashboard?trello=connected")


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