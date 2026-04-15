import aiohttp
import json
from typing import Optional, List, Dict, Any
from datetime import datetime

from models import ScrumUpdate, TrelloCard
from configs.settings import settings


class TrelloService:
    def __init__(self):
        self.api_key = settings.trello_api_key
        self.token = settings.trello_token
        self.board_id = settings.trello_board_id
        self.base_url = "https://api.trello.com/1"
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json"
        }
    
    def _auth_params(self) -> Dict[str, str]:
        return {
            "key": self.api_key,
            "token": self.token
        }
    
    async def get_board_lists(self) -> List[Dict[str, Any]]:
        if not self.api_key or not self.token or not self.board_id:
            return []
        
        session = await self._get_session()
        url = f"{self.base_url}/boards/{self.board_id}/lists"
        
        async with session.get(
            url, 
            params=self._auth_params(),
            headers=self._auth_headers()
        ) as response:
            if response.status == 200:
                return await response.json()
            return []
    
    async def create_card(
        self, 
        card: TrelloCard
    ) -> Optional[Dict[str, Any]]:
        if not self.api_key or not self.token:
            return None
        
        session = await self._get_session()
        url = f"{self.base_url}/cards"
        
        data = {
            "name": card.name,
            "desc": card.desc or "",
            "idList": card.idList
        }
        
        if card.idMembers:
            data["idMembers"] = ",".join(card.idMembers)
        if card.due:
            data["due"] = card.due.isoformat()
        if card.labels:
            data["labels"] = ",".join(card.labels)
        
        async with session.post(
            url,
            json=data,
            params=self._auth_params(),
            headers=self._auth_headers()
        ) as response:
            if response.status == 200:
                return await response.json()
            return None
    
    async def update_card(
        self, 
        card_id: str, 
        updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not self.api_key or not self.token:
            return None
        
        session = await self._get_session()
        url = f"{self.base_url}/cards/{card_id}"
        
        params = self._auth_params()
        params.update(updates)
        
        async with session.put(
            url, 
            params=params,
            headers=self._auth_headers()
        ) as response:
            if response.status == 200:
                return await response.json()
            return None
    
    async def create_card_from_scrum(
        self, 
        scrum: ScrumUpdate, 
        list_id: str
    ) -> Optional[Dict[str, Any]]:
        card = TrelloCard(
            name=scrum.task,
            desc=scrum.description or "",
            idList=list_id,
            labels=[scrum.priority, scrum.status]
        )
        return await self.create_card(card)
    
    async def get_cards_in_list(
        self, 
        list_id: str
    ) -> List[Dict[str, Any]]:
        if not self.api_key or not self.token:
            return []
        
        session = await self._get_session()
        url = f"{self.base_url}/lists/{list_id}/cards"
        
        async with session.get(
            url, 
            params=self._auth_params(),
            headers=self._auth_headers()
        ) as response:
            if response.status == 200:
                return await response.json()
            return []


trello_service = TrelloService()