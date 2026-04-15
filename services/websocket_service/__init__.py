import asyncio
import json
from typing import Dict, Any, Set, Optional
from dataclasses import dataclass

import websockets
from fastapi import WebSocket

from models import LiveUpdate, ScrumUpdate
from configs.settings import settings


@dataclass
class Client:
    websocket: WebSocket
    session_id: str


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[Client]] = {}
    
    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        
        if session_id not in self.active_connections:
            self.active_connections[session_id] = set()
        
        self.active_connections[session_id].add(Client(websocket, session_id))
    
    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self.active_connections:
            self.active_connections[session_id] = {
                c for c in self.active_connections[session_id]
                if c.websocket != websocket
            }
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
    
    async def broadcast(self, message: Dict[str, Any], session_id: str):
        if session_id not in self.active_connections:
            return
        
        disconnected = set()
        
        for client in self.active_connections[session_id]:
            try:
                await client.websocket.send_json(message)
            except Exception:
                disconnected.add(client)
        
        for client in disconnected:
            self.disconnect(client.websocket, session_id)
    
    async def send_personal(self, message: Dict[str, Any], websocket: WebSocket):
        try:
            await websocket.send_json(message)
        except Exception:
            pass
    
    async def broadcast_live_update(
        self, 
        update: LiveUpdate, 
        session_id: str
    ):
        await self.broadcast(
            {
                "type": "live_update",
                "data": update.model_dump()
            },
            session_id
        )
    
    async def broadcast_scrum_update(
        self, 
        scrum: ScrumUpdate, 
        session_id: str
    ):
        await self.broadcast(
            {
                "type": "scrum_update",
                "data": scrum.model_dump()
            },
            session_id
        )


class WebSocketServer:
    def __init__(self):
        self.manager = ConnectionManager()
        self._server: Optional[websockets.WebSocketServer] = None
    
    async def start(self):
        self._server = await websockets.serve(
            self.handle_websocket,
            settings.ws_host,
            settings.ws_port
        )
        print(f"WebSocket server started on ws://{settings.ws_host}:{settings.ws_port}")
    
    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
    
    async def handle_websocket(self, websocket: WebSocket, path: str):
        session_id = path.lstrip("/")
        
        await self.manager.connect(websocket, session_id)
        
        try:
            while True:
                raw = await websocket.recv()
                data = json.loads(raw)
                await self.handle_message(data, websocket, session_id)
        except websockets.exceptions.ConnectionClosed:
            self.manager.disconnect(websocket, session_id)
        except Exception as e:
            print(f"WebSocket error: {e}")
            self.manager.disconnect(websocket, session_id)
    
    async def handle_message(
        self, 
        data: Dict[str, Any], 
        websocket: WebSocket, 
        session_id: str
    ):
        msg_type = data.get("type")
        
        if msg_type == "ping":
            await self.manager.send_personal(
                {"type": "pong"},
                websocket
            )
        elif msg_type == "subscribe":
            await self.manager.send_personal(
                {"type": "subscribed", "session_id": session_id},
                websocket
            )


connection_manager = ConnectionManager()