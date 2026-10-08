from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any, Literal
from uuid import UUID
from datetime import datetime

# A share-sheet URL with tracking parameters can be long, but not unbounded.
MAX_URL_CHARS = 4096
# The app sends at most 20 per batch; the ceiling only stops a runaway client.
MAX_SYNC_BATCH = 100


class IngestRequest(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL_CHARS)
    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        from app.utils.url_safety import validate_url
        return validate_url(value)

    preview: Optional[str] = None
    title_hint: Optional[str] = None

class IngestResponse(BaseModel):
    id: UUID
    status: str
    canonical_url: str
    already_exists: bool = False

class SyncItem(BaseModel):
    client_id: str
    url: str = Field(min_length=1, max_length=MAX_URL_CHARS)
    captured_at: Optional[str] = None
    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        from app.utils.url_safety import validate_url
        return validate_url(value)

    preview: Optional[str] = None
    title_hint: Optional[str] = None

class SyncBatchRequest(BaseModel):
    items: List[SyncItem] = Field(max_length=MAX_SYNC_BATCH)

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
    topics: List[str] = []
    content_type: Optional[str] = None
    entities: Dict[str, Any] = {}
    likely_intent: Optional[str] = None
    suggested_action: Optional[str] = None
    intent: Optional[str] = None
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

    instant_brief: Optional[str] = None
    best_takeaway: Optional[str] = None
    key_points_with_refs: List[Dict[str, Any]] = []
    content_type: Optional[str] = None
    confidence: Optional[str] = None
    evidence_level: Optional[str] = None
    missing_info: Optional[str] = None
    search_phrases: List[str] = []
    topics: List[str] = []
    likely_intent: Optional[str] = None
    suggested_action: Optional[str] = None
    evidence_used: List[str] = []
    transcript: List[Dict[str, Any]] = []
    ocr_text: str = ""
    prompt_version: Optional[str] = None
    brief_source: str | None = None
    needs_retry: bool = False
    processing_metadata: Dict[str, Any] = {}

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _read_v2(cls, value):
        if isinstance(value, dict):
            return value
        data = {name: getattr(value, name) for name in cls.model_fields if hasattr(value, name)}
        brief = getattr(value, "brief_v2", None) or {}
        if "topics" not in brief:
            data["topics"] = ((getattr(value, "fetch_metadata", None) or {}).get("brief") or {}).get("topics", [])
        evidence = getattr(value, "evidence_bundle", None) or {}
        metadata = getattr(value, "processing_metadata", None) or {}
        for name in ("instant_brief", "best_takeaway", "content_type", "confidence", "missing_info",
                     "search_phrases", "topics", "likely_intent", "suggested_action", "evidence_used"):
            if name in brief: data[name] = brief[name]
        data.update(key_points_with_refs=brief.get("key_points", []),
                    evidence_level=evidence.get("evidence_level"),
                    transcript=evidence.get("transcript", []), ocr_text=evidence.get("ocr_text", ""),
                    prompt_version=metadata.get("prompt_version"), processing_metadata=metadata,
                    brief_source=brief.get("brief_source"),
                    needs_retry=bool(getattr(value, "needs_retry", False)))
        return data

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


class BriefPoint(BaseModel):
    point: str = Field(min_length=1)
    source_ref: Optional[str] = None
    segment_ids: List[int] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


class BriefEntities(BaseModel):
    tools_products: List[str]
    people_orgs: List[str]
    numbers: List[str]
    model_config = ConfigDict(extra="forbid")


class BriefV2(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    content_type: Literal["video", "article", "social_post", "recipe", "product", "tutorial",
                          "ai_tool", "image", "pdf", "news", "list", "how_to", "other"]
    instant_brief: str = Field(min_length=1)
    key_points: List[BriefPoint]
    best_takeaway: Optional[str]
    entities: BriefEntities
    topics: List[str]
    tags: List[str] = Field(min_length=15, max_length=30)
    search_phrases: List[str] = Field(min_length=5, max_length=8)
    likely_intent: Optional[str]
    suggested_action: Optional[str]
    confidence: Literal["high", "medium", "low"]
    evidence_used: List[Literal["transcript", "caption", "ocr", "metadata"]]
    missing_info: Optional[str]
    model_config = ConfigDict(extra="forbid")

    def search_document(self) -> str:
        return " ".join([self.title, self.instant_brief,
                         *[p.point for p in self.key_points], *self.tags,
                         *self.search_phrases, *self.entities.tools_products,
                         *self.entities.people_orgs, *self.entities.numbers])
