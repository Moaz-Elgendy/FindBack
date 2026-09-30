"""Small pure helpers shared by ingest paths.

Kept free of FastAPI/SQLAlchemy imports so they are unit-testable without a
database or the psycopg2 driver present.
"""
from __future__ import annotations

from typing import Optional


def truncate(value: Optional[str], limit: int) -> Optional[str]:
    """Return ``value`` clipped to ``limit`` chars, passing ``None`` through."""
    if not value:
        return None
    return value[:limit]


def derive_title(title_hint: Optional[str], preview: Optional[str]) -> Optional[str]:
    """Best-effort title for an item that has not been processed yet.

    A caller-supplied ``title_hint`` always wins. Only when it is absent do we
    fall back to the head of the ``preview``. The previous inline expression
    (``hint or preview[:120] if preview else None``) bound the conditional first
    and therefore dropped the hint whenever no preview was supplied.
    """
    hint = (title_hint or "").strip()
    if hint:
        return hint[:120]
    return truncate(preview, 120)
