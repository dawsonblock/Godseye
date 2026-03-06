import os
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class TrackObject(BaseModel):
    id: str
    kind: str  # 'aircraft', 'satellite', 'debris', 'ship'
    source: str
    ts: datetime
    lat: float
    lon: float
    alt_m: Optional[float] = None
    heading_deg: Optional[float] = None
    speed_mps: Optional[float] = None
    meta: Optional[Dict[str, Any]] = Field(default_factory=dict)


class Event(BaseModel):
    event_id: str
    kind: str
    ts: datetime
    lat: Optional[float] = None
    lon: Optional[float] = None
    meta: Optional[Dict[str, Any]] = Field(default_factory=dict)
