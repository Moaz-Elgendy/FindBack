"""Runs the Phase 19 evaluation end to end and prints the report.

The corpus is ingested through the *real* pipeline -- the same FETCH, NORMALIZE,
UNDERSTAND, BRIEF, CHUNK, EMBED stages that a real save goes through -- so the
suite measures the shipped retrieval path rather than a re-implementation of it.

The only thing that is ever substituted is the AI gateway, and only when no
provider is configured:

    with a provider    the real model is asked, and these numbers describe it
    without one        a deterministic oracle stands in, and these numbers
                       describe the retrieval machinery, NOT model quality

`provider_mode()` reports which of the two produced a given run, because
presenting oracle numbers as model numbers would be the worst possible outcome
of an evaluation suite.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.schemas import Brief
from app.services import ai_gateway, profiles
from app.services.pipeline import search_document

from evals.dataset import DOCUMENTS, DOCUMENTS_BY_KEY, QUERIES, Document, Query
from evals.metrics import (
    SEMANTIC_ONLY_REASON, RetrievalCase, brief_usefulness,
    explanation_correctness, extraction_correctness, groundedness,
    retrieval_report,
)

DIM = 1536


# --- the offline stand-in ---------------------------------------------------

def _tokens(text: str) -> list[str]:
    from evals.metrics import tokens as tokenise

    return tokenise(text)


class OracleProvider:
    """A deterministic stand-in used only when no provider is configured.

    Embeddings are an IDF-weighted bag of tokens over the evaluation corpus:
    distinctive words count for more than common ones, which is enough for
    cosine ranking to be meaningful. Generation answers with the gold brief for
    the document it recognises, so brief metrics still measure what the pipeline
    does with a brief -- but not whether a model could have written one.
    """
    name = "oracle"

    def __init__(self, corpus: Sequence[Document]):
        self.corpus = list(corpus)
        self._by_key: dict[str, dict] = {}
        document_frequency: dict[str, int] = {}
        for doc in self.corpus:
            self._by_key[doc.key] = doc.brief
            seen = set(_tokens(doc.body + " " + doc.title))
            for token in seen:
                document_frequency[token] = document_frequency.get(token, 0) + 1
        # Stopwords and very common words carry no signal even in a bag of words.
        document_frequency.update(
            {token: len(self.corpus) + 1
             for token in _tokens(" ".join(d.title for d in self.corpus))})
        self._idf = {
            token: 1.0 / (1.0 + count)
            for token, count in document_frequency.items()
        }
        self._vocab = sorted(self._idf)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIM
        for token in set(_tokens(text)):
            index = self._vocab.index(token) if token in self._vocab else -1
            if index >= 0 and index < DIM:
                vector[index] = self._idf[token]
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]

    async def generate_json(self, system_prompt: str, user_prompt: str, *,
                            temperature: float = 0.0) -> dict[str, Any]:
        from evals.metrics import tokens as tokenise

        prompt_tokens = set(tokenise(user_prompt))
        best_key, best_score = None, 0.0
        for doc in self.corpus:
            # How much of this document's own vocabulary is in the prompt.
            body_tokens = set(tokenise(doc.body))
            if not body_tokens:
                continue
            score = len(prompt_tokens & body_tokens) / len(body_tokens)
            if score > best_score:
                best_key, best_score = doc.key, score
        if best_key is None:
            raise ValueError("oracle could not identify the document")
        return dict(self._by_key[best_key])

    async def embed(self, texts, *, task: str = "document"):
        return [self._vector(text) for text in texts]

    async def embed_one(self, text: str, *, task: str = "document"):
        return self._vector(text)

    def model_name(self) -> str:
        return "oracle-eval-model"


def provider_mode() -> str:
    """Which kind of run produced these numbers."""
    if os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY") or \
            os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "provider"
    return "oracle"


# --- expected profile per content kind --------------------------------------

# The gold label for each document kind, and the structured_data keys that
# profile is obliged to produce (Phase 10). Taken from app.services.profiles so
# the suite cannot drift from the shipped profiles.
EXPECTED_PROFILE = {
    "video": ("list", profiles.LIST.keys),
    "recipe": ("recipe", profiles.RECIPE.keys),
    "product": ("product", profiles.PRODUCT.keys),
    "tutorial": ("tutorial", profiles.TUTORIAL.keys),
    "ai_tool": ("general", profiles.GENERAL.keys),
    "article": ("general", profiles.GENERAL.keys),
    "post": ("general", profiles.GENERAL.keys),
}


@dataclass
class CorpusEntry:
    """One ingested document, as the pipeline left it."""

    key: str
    item_id: str
    search_text: str
    brief: dict[str, Any]
    expected_profile: str


@dataclass
class EvaluationResult:
    retrieval: Any
    grounded_score: float
    grounded_checked: int
    grounded_failures: dict[str, list[str]]
    usefulness_score: float
    usefulness_notes: dict[str, list[str]]
    extraction_rows: list[str]
    extraction_correct: int
    explanation_correct: int
    explanation_total: int
    # Results that matched on meaning only: the reason names no term at all.
    semantic_only: list[str]
    # Results whose reason claimed a term that is not in the indexed text.
    wrong_reasons: list[str]
    provider_mode: str
    per_query: list[dict] = field(default_factory=list)
    # The subset of extraction rows where classification or profile keys were
    # wrong. Kept last so it does not precede a non-default field.
    wrong_extractions: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        lines = [
            f"provider: {self.provider_mode}",
            "",
            "Retrieval",
            *self.retrieval.as_rows(),
            "",
            f"Brief groundedness   {self.grounded_score:.3f} "
            f"({self.grounded_checked} statements checked)",
            f"Brief usefulness     {self.usefulness_score:.3f}",
            f"Extraction correct   {self.extraction_correct}/"
            f"{len(self.extraction_rows)}",
            f"Explanations valid   {self.explanation_correct}/"
            f"{self.explanation_total}",
            f"Semantic-only match  {len(self.semantic_only)}",
        ]
        if self.retrieval.missing:
            lines += ["", "gold never returned:"] + [
                f"  - {q}" for q in self.retrieval.missing]
        if self.retrieval.buried:
            lines += ["", "returned but not first:"] + [
                f"  - {q}" for q in self.retrieval.buried]
        if self.wrong_reasons:
            lines += ["", "reasons claiming a term that is not there:"] + [
                f"  - {r}" for r in self.wrong_reasons]
        if self.wrong_extractions:
            lines += ["", "extraction mismatches:"] + [
                f"  - {row}" for row in self.wrong_extractions]
        return "\n".join(lines)

    def as_json(self) -> str:
        return json.dumps({
            "provider_mode": self.provider_mode,
            "recall_at_1": self.retrieval.recall_at_1,
            "recall_at_5": self.retrieval.recall_at_5,
            "mrr": self.retrieval.mrr,
            "queries": self.retrieval.total,
            "groundedness": self.grounded_score,
            "usefulness": self.usefulness_score,
            "extraction_correct": self.extraction_correct,
            "extraction_total": len(self.extraction_rows),
            "explanation_correct": self.explanation_correct,
            "explanation_total": self.explanation_total,
            "semantic_only": self.semantic_only,
            "wrong_reasons": self.wrong_reasons,
            "groundedness_failures": self.grounded_failures,
            "per_query": self.per_query,
        }, indent=2, ensure_ascii=False)


# --- ingestion --------------------------------------------------------------

def ingest_corpus(db_engine, sessions, corpus: Sequence[Document]) -> list[CorpusEntry]:
    """Run every document through the real pipeline.

    FETCH is stubbed to return the document's body -- that is the only part of
    the pipeline the corpus cannot supply for real. UNDERSTAND and EMBED go
    through the gateway, so the brief and the vectors are produced exactly as a
    live save produces them.
    """
    import app.models as models
    from app import database as db_module
    import app.tasks as app_tasks
    from app.services import fetcher, storage

    entries: list[CorpusEntry] = []
    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(models.User.__table__.insert().values(
            id=uid, email=f"eval-{uid.hex[:8]}@findback.local"))
        s.commit()

    async def _fetch(url, preview=""):
        for doc in corpus:
            if doc.key in url:
                return {"text": doc.body, "title": doc.title,
                        "source_type": doc.source_type}
        return {"text": "", "title": ""}

    fetcher.fetch_content = _fetch
    storage.store_raw_snapshot = lambda item_id, payload: None

    previous_engine = db_module._engine
    db_module._engine = db_engine
    db_module._sessionmaker = None
    try:
        for doc in corpus:
            url = f"https://eval.findback.test/{doc.key}"
            with sessions() as s:
                asset = models.ContentAsset(
                    canonical_url=url, dedupe_key=f"url:{url}",
                    owner_user_id=uid, visibility="UNKNOWN",
                    source_type=doc.source_type)
                s.add(asset)
                s.flush()
                # Phase 16's composite FK requires the memory row to exist
                # before the item that points at it.
                memory = models.UserMemory(user_id=uid, content_id=asset.id)
                s.add(memory)
                s.flush()
                item = models.Item(user_id=uid, url=url, canonical_url=url,
                                   title=doc.title, content_id=asset.id,
                                   source_domain="eval.findback.test")
                s.add(item)
                s.flush()
                item_id = str(item.id)
                s.commit()
            app_tasks.process_item.apply(args=(item_id,), throw=False)

            with sessions() as s:
                row = s.execute(models.Item.__table__.select().where(
                    models.Item.__table__.c.id == item_id)).mappings().one()
                brief = (row["fetch_metadata"] or {}).get("brief") or {}
            expected_profile, expected_keys = EXPECTED_PROFILE[doc.kind]
            entries.append(CorpusEntry(
                key=doc.key, item_id=item_id,
                search_text=row["search_text"] or "",
                brief=brief, expected_profile=expected_profile))
    finally:
        db_module._engine = previous_engine
        db_module._sessionmaker = None
    return entries


# --- measurement ------------------------------------------------------------

def run_queries(db_engine, uid, queries: Sequence[Query],
                key_by_item: dict[str, str]) -> tuple[list[RetrievalCase], list[dict]]:
    """Search for every query and record what came back."""
    from app.services import search as search_service

    cases: list[RetrievalCase] = []
    detail: list[dict] = []
    for query in queries:
        with Session(bind=db_engine) as session:
            results, _took = asyncio.run(
                search_service.hybrid_search(session, uid, query.text, limit=10))
        ranked, reasons = [], []
        for result in results:
            item_id = str(result["row"][0])
            ranked.append(key_by_item.get(item_id, item_id))
            reasons.append(result["match_reason"])
        cases.append(RetrievalCase(query=query.text, gold=query.gold,
                                    ranked=ranked, returned=list(ranked)))
        detail.append({"query": query.text, "gold": list(query.gold),
                       "ranked": list(ranked), "reasons": reasons})
    return cases, detail


def evaluate(cases: Sequence[RetrievalCase], detail: Sequence[dict],
             entries: Sequence[CorpusEntry],
             corpus: Sequence[Document]) -> EvaluationResult:
    """Turn the run into the numbers the phase asks to be reported."""
    by_key = {entry.key: entry for entry in entries}
    docs = {doc.key: doc for doc in corpus}

    grounded_checked = grounded_supported = 0
    grounded_failures: dict[str, list[str]] = {}
    usefulness_total = usefulness_passed = 0.0
    usefulness_notes: dict[str, list[str]] = {}
    extraction_rows: list[str] = []
    extraction_correct = 0
    wrong_extractions: list[str] = []
    explanation_correct = explanation_total = 0
    semantic_only: list[str] = []
    wrong_reasons: list[str] = []

    for entry in entries:
        doc = docs[entry.key]
        structured = entry.brief.get("structured_data") or {}

        # Factuality: is every checkable statement supported by the source?
        result = groundedness(entry.brief, doc.body,
                              structured.get("content_type"))
        grounded_checked += result.checked
        grounded_supported += result.supported
        if result.unsupported:
            grounded_failures[entry.key] = result.unsupported

        # Usefulness: does the brief carry what the user would want back?
        _, expected_keys = EXPECTED_PROFILE[doc.kind]
        usefulness = brief_usefulness(entry.brief, doc.kind, doc.source_type,
                                     expected_keys)
        usefulness_total += usefulness.score
        usefulness_passed += usefulness.score
        if usefulness.notes:
            usefulness_notes[entry.key] = usefulness.notes

        # Extraction: right profile, and the keys that profile must produce.
        extraction = extraction_correctness(entry.brief, entry.expected_profile,
                                            expected_keys)
        extraction_rows.append(f"{entry.key}: {extraction.as_row()}")
        if extraction.correct:
            extraction_correct += 1
        else:
            wrong_extractions.append(f"{entry.key}: {extraction.as_row()}")

    # Explanations: every claimed term must really be in the indexed text.
    # A "similar meaning" reason claims no term, so it is counted as valid but
    # reported separately -- it says the match was semantic and nothing more.
    for case, query_detail in zip(cases, detail):
        for rank, key in enumerate(case.ranked):
            entry = by_key.get(key)
            haystack = entry.search_text if entry else ""
            haystack = haystack or docs[key].body
            reason = (query_detail["reasons"][rank]
                      if rank < len(query_detail["reasons"]) else "")
            if reason.strip().lower() == SEMANTIC_ONLY_REASON:
                semantic_only.append(f"{case.query!r} -> {key}")
                continue
            explanation_total += 1
            ok, _offenders = explanation_correctness(reason, haystack)
            if ok:
                explanation_correct += 1
            else:
                wrong_reasons.append(f"{case.query!r} -> {key}: {reason!r}")

    grounded_score = (grounded_supported / grounded_checked
                      if grounded_checked else 0.0)
    return EvaluationResult(
        retrieval=retrieval_report(cases),
        grounded_score=grounded_score,
        grounded_checked=grounded_checked,
        grounded_failures=grounded_failures,
        usefulness_score=(usefulness_passed / len(entries) if entries else 0.0),
        usefulness_notes=usefulness_notes,
        extraction_rows=extraction_rows,
        extraction_correct=extraction_correct,
        wrong_extractions=wrong_extractions,
        explanation_correct=explanation_correct,
        explanation_total=explanation_total,
        semantic_only=semantic_only,
        wrong_reasons=wrong_reasons,
        provider_mode=provider_mode(),
        per_query=list(detail),
    )


def run_evaluation(db_engine, sessions, corpus: Sequence[Document] = DOCUMENTS,
                   queries: Sequence[Query] = QUERIES) -> EvaluationResult:
    """Ingest the corpus, run every query, and return the measured result.

    The gateway is swapped for the oracle only when no provider is configured,
    and restored afterwards, so a provider-backed run is unaffected.
    """
    previous = ai_gateway.get_gateway()
    if provider_mode() != "provider":
        ai_gateway.set_gateway(
            ai_gateway.Gateway(adapter=OracleProvider(corpus)))
    try:
        entries = ingest_corpus(db_engine, sessions, corpus)
        key_by_item = {entry.item_id: entry.key for entry in entries}
        uid = _ingested_user_id(sessions)
        cases, detail = run_queries(db_engine, uid, queries, key_by_item)
        return evaluate(cases, detail, entries, corpus)
    finally:
        ai_gateway.set_gateway(previous)


def _ingested_user_id(sessions) -> str:
    """The user the corpus was saved under."""
    from sqlalchemy import text as sql

    with sessions() as s:
        row = s.execute(sql(
            "SELECT id FROM users WHERE email LIKE 'eval-%' LIMIT 1")).first()
    if row is None:
        raise RuntimeError("the corpus was not ingested")
    return str(row[0])





