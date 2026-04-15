from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime


class SpeakerSegment(BaseModel):
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    speaker: str = Field(..., description="Speaker identifier")


class TranscriptSegment(BaseModel):
    speaker: str = Field(..., description="Speaker identifier")
    text: str = Field(..., description="Transcribed text")
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class AudioEvent(BaseModel):
    session_id: str
    timestamp: float
    sample_rate: int = 16000
    duration: float
    speaker_segments: List[SpeakerSegment] = []
    transcripts: List[TranscriptSegment] = []


class VideoEvent(BaseModel):
    session_id: str
    timestamp: float
    frame_count: int = 1
    descriptions: List[str] = []
    visual_context: Optional[str] = None


class FusionEvent(BaseModel):
    session_id: str
    timestamp: float
    speaker: Optional[str] = None
    text: Optional[str] = None
    visual_context: Optional[str] = None
    audio_source: Optional[TranscriptSegment] = None
    video_source: Optional[VideoEvent] = None


class ScrumUpdate(BaseModel):
    task: str
    status: Literal["todo", "in-progress", "done", "blocked"] = "todo"
    owner: Optional[str] = None
    priority: Literal["low", "medium", "high"] = "medium"
    description: Optional[str] = None
    blocker: Optional[str] = None
    decision: Optional[str] = None
    timestamp: float


class MeetingSession(BaseModel):
    session_id: str
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    fusion_events: List[FusionEvent] = []
    scrum_updates: List[ScrumUpdate] = []
    summary: Optional[str] = None
    tasks: List[Dict[str, Any]] = []
    decisions: List[str] = []
    blockers: List[str] = []


class LiveUpdate(BaseModel):
    type: Literal["scrum_update", "task_created", "decision", "blocker"]
    data: Dict[str, Any]
    timestamp: float
    session_id: str


class TrelloCard(BaseModel):
    name: str
    desc: Optional[str] = None
    idList: str
    idMembers: List[str] = []
    due: Optional[datetime] = None
    labels: List[str] = []