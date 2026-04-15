import asyncio
import json
from typing import List, Optional, Dict, Any, AsyncIterator

from models import FusionEvent, ScrumUpdate, LiveUpdate, MeetingSession
from core import get_redis, RedisClient
from configs.settings import settings


class QwenReasoningService:
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.redis: Optional[RedisClient] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def initialize(self):
        try:
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM
            self.tokenizer = AutoTokenizer.from_pretrained(settings.qwen_model)
            self.model = AutoModelForCausalLM.from_pretrained(
                settings.qwen_model,
                torch_dtype=torch.float16 if self._get_device() == "cuda" else torch.float32
            )
            self.model.to(self._get_device())
        except Exception as e:
            print(f"[reasoning_service] Qwen model unavailable: {e}")

        self.redis = await get_redis()

    def _get_device(self) -> str:
        return settings.qwen_device

    async def analyze_meeting_event(self, event: FusionEvent) -> Optional[ScrumUpdate]:
        if not self.model or not self.tokenizer or not event.text:
            return None
        prompt = self._build_scrum_prompt(event)
        try:
            import torch
            messages = [{"role": "user", "content": prompt}]
            inputs = self.tokenizer.apply_chat_template(
                messages, tokenize=True, return_tensors="pt"
            ).to(self._get_device())
            with torch.no_grad():
                outputs = self.model.generate(inputs, max_new_tokens=256, temperature=0.7, do_sample=True)
            response = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            return self._parse_scrum_update(response, event.timestamp)
        except Exception as e:
            print(f"[reasoning_service] Reasoning error: {e}")
            return None

    def _build_scrum_prompt(self, event: FusionEvent) -> str:
        context_parts = []
        if event.speaker:
            context_parts.append(f"Speaker {event.speaker} said: {event.text}")
        if event.visual_context:
            context_parts.append(f"Visual: {event.visual_context}")
        context = "\n".join(context_parts) if context_parts else event.text or ""
        return f"""You are an AI Scrum Master analyzing a meeting.\n\nContext:\n{context}\n\nRespond in JSON format:\n{{\n  "task": "task name or null",\n  "status": "todo|in-progress|done|blocked",\n  "owner": "owner name or null",\n  "priority": "low|medium|high",\n  "description": "brief description or null",\n  "blocker": "blocker description or null",\n  "decision": "decision made or null"\n}}\n\nIf no Scrum information found, respond with all null values."""

    def _parse_scrum_update(self, response: str, timestamp: float) -> Optional[ScrumUpdate]:
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                task_value = data.get("task")
                if not task_value or not task_value.strip():
                    return None
                return ScrumUpdate(
                    task=task_value,
                    status=data.get("status", "todo"),
                    owner=data.get("owner"),
                    priority=data.get("priority", "medium"),
                    description=data.get("description"),
                    blocker=data.get("blocker"),
                    decision=data.get("decision"),
                    timestamp=timestamp
                )
        except Exception as e:
            print(f"[reasoning_service] Parse error: {e}")
        return None

    async def process_fusion_events(self, event_iterator: AsyncIterator[FusionEvent], session_id: str) -> List[ScrumUpdate]:
        updates = []
        self._running = True
        while self._running:
            try:
                event = await asyncio.wait_for(event_iterator.__anext__(), timeout=5.0)
                update = await self.analyze_meeting_event(event)
                if update and (update.task or update.blocker or update.decision):
                    updates.append(update)
                    if self.redis:
                        await self.redis.publish_event(
                            settings.live_updates_stream,
                            LiveUpdate(type="scrum_update", data=update.model_dump(), timestamp=update.timestamp, session_id=session_id).model_dump()
                        )
                        await self.redis.append_to_session_list(session_id, "scrum_updates", update.model_dump())
            except asyncio.TimeoutError:
                continue
            except StopAsyncIteration:
                break
            except Exception as e:
                print(f"[reasoning_service] Processing error: {e}")
                break
        return updates

    async def start_session(self, session_id: str):
        self._running = True

    async def stop_session(self, session_id: str):
        self._running = False
        if self._task:
            self._task.cancel()


class DeepSeekRefinementService:
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.redis: Optional[RedisClient] = None

    async def initialize(self):
        try:
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM
            self.tokenizer = AutoTokenizer.from_pretrained(settings.deepseek_model)
            self.model = AutoModelForCausalLM.from_pretrained(
                settings.deepseek_model,
                torch_dtype=torch.float16 if self._get_device() == "cuda" else torch.float32
            )
            self.model.to(self._get_device())
        except Exception as e:
            print(f"[reasoning_service] DeepSeek model unavailable: {e}")

        self.redis = await get_redis()

    def _get_device(self) -> str:
        return settings.deepseek_device

    async def refine_session(self, session_id: str) -> MeetingSession:
        if not self.redis:
            return MeetingSession(session_id=session_id)
        fusion_events_data = await self.redis.get_session_list(session_id, "fusion_events")
        scrum_updates_data = await self.redis.get_session_list(session_id, "scrum_updates")
        if not fusion_events_data:
            return MeetingSession(session_id=session_id)
        fusion_events = [FusionEvent(**e) for e in fusion_events_data]
        scrum_updates = [ScrumUpdate(**u) for u in scrum_updates_data]
        prompt = self._build_refinement_prompt(fusion_events, scrum_updates)
        refined = await self._run_refinement(prompt)
        return MeetingSession(
            session_id=session_id,
            fusion_events=fusion_events,
            scrum_updates=scrum_updates,
            summary=refined.get("summary"),
            tasks=refined.get("tasks", []),
            decisions=refined.get("decisions", []),
            blockers=refined.get("blockers", [])
        )

    def _build_refinement_prompt(self, fusion_events: List[FusionEvent], scrum_updates: List[ScrumUpdate]) -> str:
        events_text = "\n".join([f"[{e.timestamp}] {e.speaker}: {e.text}" for e in fusion_events if e.text])
        updates_text = "\n".join([f"- {u.task}: {u.status} (owner: {u.owner})" for u in scrum_updates if u.task])
        return f"""You are an AI Scrum Master conducting post-meeting refinement.\n\nMeeting Events:\n{events_text}\n\nExtracted Scrum Updates:\n{updates_text}\n\nRespond in JSON format:\n{{\n  "summary": "meeting summary",\n  "tasks": [{{"task": "name", "status": "todo|in-progress|done", "owner": "name"}}],\n  "decisions": ["decision 1"],\n  "blockers": ["blocker 1"]\n}}"""

    async def _run_refinement(self, prompt: str) -> Dict[str, Any]:
        if not self.model or not self.tokenizer:
            return {}
        try:
            import torch
            messages = [{"role": "user", "content": prompt}]
            inputs = self.tokenizer.apply_chat_template(
                messages, tokenize=True, return_tensors="pt"
            ).to(self._get_device())
            with torch.no_grad():
                outputs = self.model.generate(inputs, max_new_tokens=512, temperature=0.5, do_sample=True)
            response = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(response[json_start:json_end])
        except Exception as e:
            print(f"[reasoning_service] Refinement error: {e}")
        return {}


qwen_service = QwenReasoningService()
deepseek_service = DeepSeekRefinementService()
