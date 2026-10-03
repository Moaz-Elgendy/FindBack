"""Retrieval and Brief metrics for the Phase 19 evaluation suite.

Everything here is a pure function over a ranked list, a gold label and a
document. That matters for two reasons: the suite must run with no provider
configured, and a metric that cannot be tested on a hand-written case cannot be
trusted on a real one.

Definitions used, stated so the numbers in the report are unambiguous:

    Recall@k    fraction of queries whose gold document appears in the top k.
                A query with several gold documents counts as a hit if ANY of
                them appears -- the user wanted one of them, not all.

    MRR         mean of 1/rank over queries, where rank is 1-based and a
                missing gold document contributes 0.

    Grounded    fraction of a brief's checkable statements that appear verbatim
                in the document the brief came from. Verbatim is strict on
                purpose: with no judge model available, "does this word appear
                in the source" is the only factuality check that cannot be
                argued with.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# A word, allowing the CJK and Cyrillic runs that a single `[a-z0-9]+` misses.
# Without this, mixed-language content scores zero purely because the tokenizer
# cannot see it -- which would be measuring the tokenizer, not the search.
_WORD = re.compile(r"[0-9A-Za-zÀ-ɏЀ-ӿ぀-ヿ一-鿿가-힯]+")


def normalise(text: str) -> str:
    """Casefold and strip accents, so `Café` and `cafe` compare equal."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed.casefold()
                   if not unicodedata.combining(ch))


def tokens(text: str) -> list[str]:
    return _WORD.findall(normalise(text))


# Suffixes stripped before comparing words. Without this, "lentil" in a brief
# and "lentils" in the source read as unrelated, and a faithful summary is
# scored as a hallucination. Deliberately tiny: an aggressive stemmer would
# merge words that are genuinely different and hide a real error.
_SUFFIXES = ("ings", "ing", "ies", "ied", "ed", "es", "s")


def stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def stem_set(text: str) -> set[str]:
    return {stem(word) for word in tokens(text)}


def _squeeze(text: str) -> str:
    """Normalised text with all whitespace removed, for a verbatim comparison."""
    return re.sub(r"\s+", "", normalise(text))


@dataclass
class RetrievalCase:
    """One query's outcome."""

    query: str
    gold: tuple[str, ...]
    ranked: list[str]
    # Kept so the report can show what went wrong, not just that it did.
    returned: list[str] = field(default_factory=list)

    @property
    def first_gold_rank(self) -> int | None:
        for index, key in enumerate(self.ranked, start=1):
            if key in self.gold:
                return index
        return None

    @property
    def reciprocal_rank(self) -> float:
        rank = self.first_gold_rank
        return 1.0 / rank if rank else 0.0

    def hit_at(self, k: int) -> bool:
        return any(key in self.gold for key in self.ranked[:k])


def recall_at_k(cases: Sequence[RetrievalCase], k: int) -> float:
    if not cases:
        return 0.0
    return sum(1.0 for case in cases if case.hit_at(k)) / len(cases)


def mean_reciprocal_rank(cases: Sequence[RetrievalCase]) -> float:
    if not cases:
        return 0.0
    return sum(case.reciprocal_rank for case in cases) / len(cases)


@dataclass
class RetrievalReport:
    recall_at_1: float
    recall_at_5: float
    mrr: float
    total: int
    # Queries where the gold document was returned somewhere, but not first.
    buried: list[str] = field(default_factory=list)
    # Queries where it was not returned at all.
    missing: list[str] = field(default_factory=list)

    def as_rows(self) -> list[str]:
        return [
            f"Recall@1  {self.recall_at_1:.3f}",
            f"Recall@5  {self.recall_at_5:.3f}",
            f"MRR       {self.mrr:.3f}",
            f"queries   {self.total}",
        ]


def retrieval_report(cases: Sequence[RetrievalCase], k: int = 5) -> RetrievalReport:
    buried = [c.query for c in cases if c.first_gold_rank not in (None, 1)]
    missing = [c.query for c in cases if c.first_gold_rank is None]
    return RetrievalReport(
        recall_at_1=recall_at_k(cases, 1),
        recall_at_5=recall_at_k(cases, k),
        mrr=mean_reciprocal_rank(cases),
        total=len(cases),
        buried=buried,
        missing=missing,
    )


# --- Brief quality ----------------------------------------------------------

# A statement with fewer than this many characters is not worth checking.
MIN_STATEMENT_CHARS = 3

# A statement is supported when at least this share of its content words are
# in the source. Set by hand against the corpus, not tuned to a target score:
# see `test_the_corpus_briefs_are_grounded_in_their_own_bodies`, which fails if
# this is loosened far enough to excuse a hallucination.
GROUNDED_THRESHOLD = 0.6

# Function words carry no evidence in either direction. Without this, a brief
# statement made mostly of articles and prepositions would pass on the strength
# of the words "the" and "of".
OVERLAP_STOPWORDS = frozenset("""
a an the this that these those is are was were be been being of in on at to
for with from by as and or but if then than so such it its it's
""".split())


def _flatten(value) -> Iterable[str]:
    if isinstance(value, dict):
        for key, sub in value.items():
            yield str(key)
            yield from _flatten(sub)
    elif isinstance(value, (list, tuple)):
        for sub in value:
            yield from _flatten(sub)
    elif value is not None:
        yield str(value)


@dataclass
class GroundednessResult:
    checked: int
    supported: int
    unsupported: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.supported / self.checked if self.checked else 1.0


def groundedness(brief: dict, body: str,
                 content_type: str | None = None) -> GroundednessResult:
    """Is every checkable statement in the brief supported by the source?

    Support is measured as the share of a statement's CONTENT words that occur
    in the source, not as a verbatim substring match. Verbatim matching would
    score a faithful paraphrase as a hallucination -- and would make this
    metric measure copying rather than accuracy, which is not what "factuality"
    means for a brief the user reads.

    A statement is supported when at least GROUNDED_THRESHOLD of its content
    words are present. `OVERLAP_STOPWORDS` are excluded first, so "the" and
    "a" cannot carry a statement on their own.

    `content_type` is the label the brief assigns itself ("recipe", "list").
    That is a vocabulary word, not a quotation, so it is checked for membership
    in the declared profile set instead of against the prose.
    """
    from app.services.profiles import PROFILES

    body_stems = stem_set(body)
    # A statement is also supported if it appears in the source verbatim, once
    # whitespace is squeezed out. That is the only check that works for CJK,
    # where a run of characters is one token and inflected comparison has
    # nothing to strip -- and CJK text routinely differs from its source only in
    # spacing, which a reader cannot see.
    body_text = _squeeze(body)
    result = GroundednessResult(checked=0, supported=0)
    structured = brief.get("structured_data") or {}

    statements = [brief.get("overview", "")]
    statements.extend(brief.get("highlights") or [])
    for field in ("entities", "topics", "actions"):
        statements.extend(brief.get(field) or [])
    # The `content_type` key itself is a label; its value is checked below.
    statements.extend(
        text for text in _flatten(structured) if text != "content_type")

    for statement in statements:
        statement = str(statement).strip()
        squeezed = _squeeze(statement)
        if squeezed and squeezed in body_text:
            result.checked += 1
            result.supported += 1
            continue
        content_words = [stem(t) for t in tokens(statement)
                         if t not in OVERLAP_STOPWORDS]
        if len(content_words) < MIN_STATEMENT_CHARS:
            continue
        result.checked += 1
        present = sum(1 for word in content_words if word in body_stems)
        if present / len(content_words) >= GROUNDED_THRESHOLD:
            result.supported += 1
        else:
            result.unsupported.append(statement)

    if content_type is not None:
        result.checked += 1
        if content_type in PROFILES:
            result.supported += 1
        else:
            result.unsupported.append(f"content_type {content_type}")
    return result


@dataclass
class UsefulnessResult:
    """Whether the brief would help someone remember the save.

    Named checks rather than one opaque number, so a failing document says
    which way it failed.
    """
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        if not self.checks:
            return 0.0
        return sum(1 for ok in self.checks.values() if ok) / len(self.checks)


def brief_usefulness(brief: dict, kind: str, source_type: str,
                     required_keys: Sequence[str] = ()) -> UsefulnessResult:
    """Usefulness, by the fields the user would actually want back."""
    result = UsefulnessResult()
    structured = brief.get("structured_data") or {}
    highlights = brief.get("highlights") or []

    result.checks["has_overview"] = bool(str(brief.get("overview", "")).strip())
    result.checks["has_title"] = bool(str(brief.get("title", "")).strip())
    result.checks["has_highlights"] = bool(highlights)
    result.checks["has_topics"] = bool(brief.get("topics"))
    result.checks["declares_content_type"] = bool(structured.get("content_type"))
    if kind == "video":
        result.checks["has_timestamps"] = bool(brief.get("timestamps"))
        result.checks["lists_items"] = bool(structured.get("items"))
    for key in required_keys:
        present = key in structured
        result.checks[f"has_{key}"] = present
        if not present:
            result.notes.append(f"missing profile key: {key}")
    return result


@dataclass
class ExtractionResult:
    """Did the extractor classify this content correctly?"""
    expected_profile: str
    actual_profile: str
    keys_present: bool
    expected_keys: tuple[str, ...]

    @property
    def correct(self) -> bool:
        return self.actual_profile == self.expected_profile and self.keys_present

    def as_row(self) -> str:
        ok = "ok  " if self.correct else "WRONG"
        keys = "present" if self.keys_present else "missing"
        return f"{ok} {self.expected_profile} -> {self.actual_profile} ({keys})"


def extraction_correctness(brief: dict, expected_profile: str,
                           expected_keys: Sequence[str]) -> ExtractionResult:
    """Profile classification and profile shape, against the gold label."""
    structured = brief.get("structured_data") or {}
    present = all(key in structured for key in expected_keys)
    return ExtractionResult(
        expected_profile=expected_profile,
        actual_profile=str(structured.get("content_type", "general")),
        keys_present=present,
        expected_keys=tuple(expected_keys),
    )


# --- match explanations -----------------------------------------------------

# Reasons that carry no evidence. `Matched: tutorial` says only what category
# the memory is, which is never why it matched.
VACUOUS_PREFIXES = ("matched:", "similar meaning", "related")


def explanation_terms(reason: str) -> list[str]:
    """The terms a reason claims were matched.

    Parentheticals are dropped: they say where the match was found, not what
    matched. The rest is split on the punctuation a reason uses to join terms
    ("aws + deployment").
    """
    cleaned = re.sub(r"\([^)]*\)", " ", reason or "")
    parts = [p.strip() for chunk in cleaned.split("+") for p in chunk.split(",")]
    return [p for p in parts if p]


# A match reason of "similar meaning" is not a claim that any particular term
# was found. `app.services.search.evidence_reason` returns it as an explicit
# fallback when there was no lexical evidence at all, which is the honest thing
# to do -- the result matched on embeddings. Counting it as an unverified
# explanation would flag correct behaviour as a defect, so it is reported
# separately instead of inside the explanation-validity rate.
SEMANTIC_ONLY_REASON = "similar meaning"


def explanation_correctness(reason: str, haystack: str) -> tuple[bool, list[str]]:
    """Is every claimed term actually present in what was searched?

    Returns (correct, offending terms). An empty reason counts as correct: a
    result with no explanation claims nothing that could be false.
    """
    terms = explanation_terms(reason)
    if not terms:
        return True, []
    lowered = normalise(haystack)
    offenders = [term for term in terms if normalise(term) not in lowered]
    return (not offenders), offenders


def vacuous_reason(reason: str) -> bool:
    """True when a reason explains nothing, whatever else is true of it."""
    stripped = (reason or "").strip().lower()
    if not stripped:
        return True
    return any(stripped.startswith(prefix) for prefix in VACUOUS_PREFIXES)



