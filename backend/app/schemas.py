from pydantic import BaseModel, ConfigDict, HttpUrl
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime

class IngestRequest(BaseModel):
    url: str
    preview: Optional[str] = None
    title_hint: Optional[str] = None

class IngestResponse(BaseModel):
    id: UUID
    status: str
    canonical_url: str

class SyncItem(BaseModel):
    client_id: str
    url: str
    captured_at: Optional[str] = None
    preview: Optional[str] = None
    title_hint: Optional[str] = None

class SyncBatchRequest(BaseModel):
    items: List[SyncItem]

class SyncBatchResponse(BaseModel):
    mapped: List[Dict[str, Any]]
    errors: List[Dict[str, Any]] = []

class SearchResponseItem(BaseModel):
    id: UUID
    title: Optional[str]
    summary: Optional[str]
    tags: List[str] = []
    category: Optional[str]
    thumbnail: Optional[str] = None
    source_domain: Optional[str]
    match_reason: Optional[str] = None
    score: float
    created_at: Optional[datetime]

class SearchResponse(BaseModel):
    results: List[SearchResponseItem]
    took_ms: int

class ItemDetail(BaseModel):
    id: UUID
    url: str
    canonical_url: str
    title: Optional[str]
    title_clean: Optional[str]
    source_domain: Optional[str]
    source_type: Optional[str]
    thumbnail_url: Optional[str]
    summary: Optional[str]
    key_points: List[str] = []
    category: Optional[str]
    entities: Dict[str, Any] = {}
    intent: Optional[str]
    tags: List[str] = []
    status: str
    created_at: Optional[datetime]
    processed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)

class ExtractedMemory(BaseModel):
    summary: str
    key_points: List[str]
    category: str
    entities: Dict[str, List[str]]
    intent: str
    tags: List[str]
    title_clean: str
