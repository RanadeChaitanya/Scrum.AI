from pydantic import BaseModel, Field
from pydantic import ConfigDict
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime


def _now() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)


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
    # Serialize datetime fields as ISO strings so json.dumps() never fails
    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})

    session_id: str
    started_at: datetime = Field(default_factory=_now)
    ended_at: Optional[datetime] = None
    fusion_events: List[FusionEvent] = []
    scrum_updates: List[ScrumUpdate] = []
    summary: Optional[str] = None
    tasks: List[Dict[str, Any]] = []
    decisions: List[str] = []
    blockers: List[str] = []

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Override to ensure datetime fields are serialized as ISO strings."""
        data = super().model_dump(**kwargs)
        if isinstance(data.get("started_at"), datetime):
            data["started_at"] = data["started_at"].isoformat()
        if isinstance(data.get("ended_at"), datetime):
            data["ended_at"] = data["ended_at"].isoformat()
        return data


class LiveUpdate(BaseModel):
    type: Literal["scrum_update", "task_created", "decision", "blocker"]
    data: Dict[str, Any]
    timestamp: float
    session_id: str


class TrelloCard(BaseModel):
    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})

    name: str
    desc: Optional[str] = None
    idList: str
    idMembers: List[str] = []
    due: Optional[datetime] = None
    labels: List[str] = []

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        data = super().model_dump(**kwargs)
        if isinstance(data.get("due"), datetime):
            data["due"] = data["due"].isoformat()
        return data