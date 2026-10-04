from enum import Enum
from pydantic import BaseModel, ConfigDict, field_validator
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
    # Phase 12: built from terms that really matched, e.g. "aws + deployment".
    match_reason: Optional[str] = None
    # The chunk that matched and, for timed content, when it was said.
    matched_chunk: Optional[str] = None
    matched_at: Optional[str] = None
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

class Brief(BaseModel):
    """The FindBack Brief (Phase 9).

    The generic "summary + 3 bullets" shape is replaced by these fields:
    `overview` says what this is, `highlights` carries as many entries as the
    content genuinely has (a "5 skills" video yields 5, not 3), and
    `structured_data` holds free-form JSON so a new content type needs no
    migration.

    `extra="forbid"` is the point of the phase: an unknown top-level field
    means the model invented a shape we do not store, so it is rejected rather
    than silently dropped.
    """
    title: str = ""
    overview: str = ""
    highlights: List[str] = []
    structured_data: Dict[str, Any] = {}
    entities: List[str] = []
    topics: List[str] = []
    intent: List[str] = []
    actions: List[str] = []
    timestamps: List[str] = []

    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "overview", mode="before")
    @classmethod
    def _blank_to_str(cls, value):
        # Models answer with null for "nothing to put here".
        return "" if value is None else value

    @field_validator(
        "highlights", "entities", "topics", "intent", "actions", "timestamps",
        mode="before")
    @classmethod
    def _blank_to_list(cls, value):
        # null and a bare string are both common; normalise rather than reject.
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value

    @field_validator("structured_data", mode="before")
    @classmethod
    def _blank_to_dict(cls, value):
        return {} if value is None else value

    def all_terms(self) -> List[str]:
        """Every searchable phrase in the brief, for the embedding memory string."""
        out = [self.title, self.overview, *self.highlights]
        out.extend(self.entities)
        out.extend(self.topics)
        out.extend(self.actions)
        return [t for t in out if t]


class UserIntent(str, Enum):
    """Why one user saved one piece of content.

    A closed set on purpose. The user may always write free text in a note; the
    *intent* is structured because it is the thing worth filtering and counting
    on. An open string here would collect "for my project!!!" and "later" in
    the same column and be useless for either.

    These four are the reasons the product recognises. `None` means "not
    stated", which is a real answer and is not the same as "later".
    """
    PROJECT = "project"
    TRY_LATER = "try_later"
    CONSIDER_BUYING = "consider_buying"
    RESEARCH = "research"


# Phrases that mean each intent. Deliberately small and literal: a wrong guess
# about *why* someone saved something is worse than no guess, so this only
# fires on wording that is unambiguous.
INTENT_PHRASES: Dict[str, List[str]] = {
    UserIntent.PROJECT.value: ("for my project", "my project", "work project",
                               "for work", "for the team", "side project"),
    UserIntent.TRY_LATER.value: ("try later", "try this later", "want to try",
                                 "wanna try", "try this", "later maybe"),
    UserIntent.CONSIDER_BUYING.value: ("thinking about buying", "consider buying",
                                       "might buy", "want to buy", "buy this"),
    UserIntent.RESEARCH.value: ("research this", "research later", "read again",
                                "look at this later", "study this"),
}


class UserContextIn(BaseModel):
    """What a user says about why they saved something (Phase 13).

    This is user-owned context and it is never merged into the shared content
    understanding. `note` is free text because a person reasons in sentences;
    `intent` is constrained to the enum because a machine reasons in categories.
    """
    note: Optional[str] = None
    intent: Optional[UserIntent] = None

    model_config = ConfigDict(extra="forbid")


class UserContextResponse(BaseModel):
    id: UUID
    content_id: UUID
    note: Optional[str] = None
    intent: Optional[str] = None
    save_count: int
    first_saved_at: datetime
    last_saved_at: datetime
    # The content is named so a client can render "AWS talk / For my project"
    # without a second request, but nothing here is shared with anyone else.
    content_title: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ExtractedMemory(BaseModel):
    summary: str
    key_points: List[str]
    category: str
    entities: Dict[str, List[str]]
    intent: str
    tags: List[str]
    title_clean: str
