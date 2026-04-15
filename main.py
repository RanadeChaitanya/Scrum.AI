from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import uuid
from datetime import datetime

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


@app.get("/")
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