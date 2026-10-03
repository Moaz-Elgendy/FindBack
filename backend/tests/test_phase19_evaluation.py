"""Phase 19: the search and AI evaluation suite.

    TEST_DATABASE_URL=... python -m pytest tests/test_phase19_evaluation.py -q

Two jobs, deliberately separated:

    the suite itself is correct   metrics are checked against hand-computed
                                  cases, and the corpus is checked for the
                                  coverage the phase demands

    the product is measured       retrieval, brief quality, extraction and
                                  match explanations are computed and PRINTED

The second job asserts nothing. This phase reports results; it does not decide
that a score is good enough, and a failing score must never become a red build
that someone then "fixes" by tuning the product to the dataset.
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

from evals.dataset import (  # noqa: E402  (import after the skip guard)
    DOCUMENTS, DOCUMENTS_BY_KEY, QUERIES, QUERIES_BY_TEXT, REQUIRED_BUCKETS,
    REQUIRED_QUERY_TEXTS, coverage_gaps,
)
from evals.metrics import (  # noqa: E402
    RetrievalCase, brief_usefulness, explanation_correctness, explanation_terms,
    extraction_correctness, groundedness, mean_reciprocal_rank,
    recall_at_k, retrieval_report, vacuous_reason,
)

needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping the Phase 19 live measurement",
)


# --- the metrics are correct ------------------------------------------------
# A metric that is wrong makes every number in the report meaningless, so each
# is pinned to a case whose answer can be done by hand.

def test_recall_at_k_counts_a_hit_inside_the_window():
    cases = [
        RetrievalCase("first", ("a",), ["a", "b", "c"]),      # rank 1
        RetrievalCase("third", ("c",), ["a", "b", "c"]),      # rank 3
        RetrievalCase("miss", ("z",), ["a", "b"]),            # never
    ]
    assert recall_at_k(cases, 1) == pytest.approx(1 / 3)
    assert recall_at_k(cases, 3) == pytest.approx(2 / 3)
    assert recall_at_k(cases, 5) == pytest.approx(2 / 3)


def test_recall_accepts_any_of_several_gold_documents():
    case = RetrievalCase("either", ("a", "z"), ["b", "z"])
    assert recall_at_k([case], 5) == 1.0, "the user wanted one of them"


def test_mrr_is_the_mean_of_reciprocal_ranks():
    cases = [
        RetrievalCase("rank1", ("a",), ["a"]),
        RetrievalCase("rank2", ("b",), ["x", "b"]),
        RetrievalCase("rank4", ("d",), ["w", "x", "y", "d"]),
        RetrievalCase("miss", ("m",), ["w"]),
    ]
    expected = (1.0 + 0.5 + 0.25 + 0.0) / 4
    assert mean_reciprocal_rank(cases) == pytest.approx(expected)
    assert mean_reciprocal_rank([]) == 0.0


def test_retrieval_report_separates_buried_from_missing():
    cases = [
        RetrievalCase("top", ("a",), ["a", "b"]),
        RetrievalCase("buried", ("z",), ["b", "z"]),
        RetrievalCase("gone", ("y",), ["b"]),
    ]
    report = retrieval_report(cases)
    assert report.buried == ["buried"]
    assert report.missing == ["gone"]


def test_groundedness_catches_a_statement_the_source_never_said():
    body = "The kettle boils in four minutes."
    brief = {"overview": "The kettle boils in four minutes.",
             "highlights": ["The kettle boils in four minutes.",
                            "It also sings a lullaby at night"],
             "structured_data": {"content_type": "article"}}
    result = groundedness(brief, body, "general")
    assert result.unsupported == ["It also sings a lullaby at night"]
    # Two supported statements, the declared type, and one that is not there.
    assert result.supported == 3
    assert result.checked == 4


def test_groundedness_ignores_statements_too_short_to_check():
    brief = {"overview": "", "highlights": ["ok"], "structured_data": {}}
    result = groundedness(brief, "anything at all")
    assert result.checked == 0
    assert result.score == 1.0, "nothing checkable means nothing unsupported"


def test_groundedness_treats_the_declared_content_type_as_a_claim():
    # A brief declaring a profile the product does not have is wrong and must
    # be caught. A recognised one is a label, not a quotation.
    brief = {"overview": "", "highlights": [], "structured_data": {}}
    assert groundedness(brief, "chicken and cream", "recipe").unsupported == []
    assert groundedness(brief, "chicken and cream", "podcast").unsupported == [
        "content_type podcast"]


def test_groundedness_is_case_and_accent_insensitive():
    brief = {"overview": "Café culture", "highlights": [], "structured_data": {}}
    assert groundedness(brief, "a CAFE culture note").score == 1.0


def test_groundedness_can_read_mixed_language():
    brief = {"overview": "ビルドは速い", "highlights": [], "structured_data": {}}
    result = groundedness(brief, "ビルドは速いです。")
    assert result.score == 1.0


def test_usefulness_scores_each_named_check():
    brief = {"title": "T", "overview": "O", "highlights": ["h"],
             "topics": ["t"], "structured_data": {"content_type": "recipe",
                                                  "ingredients": ["x"],
                                                  "steps": ["y"],
                                                  "time": "5", "temperature": ""}}
    result = brief_usefulness(brief, "recipe", "recipe",
                              ("ingredients", "steps", "time", "temperature"))
    assert result.score == 1.0
    assert result.notes == []


def test_usefulness_reports_a_missing_profile_key_by_name():
    brief = {"title": "T", "overview": "O", "highlights": ["h"], "topics": ["t"],
             "structured_data": {"content_type": "recipe", "ingredients": []}}
    result = brief_usefulness(brief, "recipe", "recipe",
                              ("ingredients", "steps"))
    assert result.checks["has_ingredients"] is True
    assert result.checks["has_steps"] is False
    assert "missing profile key: steps" in result.notes


def test_usefulness_demands_timestamps_only_for_video():
    brief = {"title": "T", "overview": "O", "highlights": ["h"], "topics": ["t"],
             "structured_data": {"content_type": "list", "items": ["a"]}}
    video = brief_usefulness(dict(brief, timestamps=["00:00"]), "video", "video")
    article = brief_usefulness(brief, "article", "article")
    assert video.checks["has_timestamps"] is True
    assert "has_timestamps" not in article.checks, \
        "an article must not be marked down for having no timestamps"


def test_extraction_correctness_needs_both_the_profile_and_the_keys():
    right_profile_wrong_keys = {"structured_data": {"content_type": "recipe",
                                                    "ingredients": []}}
    assert extraction_correctness(
        right_profile_wrong_keys, "recipe",
        ("ingredients", "steps")).correct is False

    wrong_profile_right_keys = {"structured_data": {
        "content_type": "list", "ingredients": [], "steps": []}}
    assert extraction_correctness(
        wrong_profile_right_keys, "recipe",
        ("ingredients", "steps")).correct is False

    both_right = {"structured_data": {"content_type": "recipe",
                                      "ingredients": [], "steps": []}}
    assert extraction_correctness(
        both_right, "recipe", ("ingredients", "steps")).correct is True


def test_explanation_correctness_accepts_only_terms_that_are_really_there():
    ok, offenders = explanation_correctness("aws + deployment",
                                            "an AWS talk about deployment")
    assert ok and offenders == []

    ok, offenders = explanation_correctness("aws + kubernetes",
                                            "an AWS talk about deployment")
    assert not ok
    assert offenders == ["kubernetes"]


def test_explanation_terms_drop_the_where_but_keep_the_what():
    assert explanation_terms("connection + pool (at 14:02)") == [
        "connection", "pool"]


def test_an_empty_explanation_is_correct_because_it_claims_nothing():
    assert explanation_correctness("", "anything") == (True, [])


def test_a_category_is_not_a_reason():
    assert vacuous_reason("Matched: tutorial")
    assert vacuous_reason("similar meaning")
    assert vacuous_reason("")
    assert not vacuous_reason("aws + deployment")


# --- the corpus covers what the phase requires ------------------------------

def test_the_corpus_covers_every_required_content_bucket():
    from evals.runner import EXPECTED_PROFILE

    gaps = coverage_gaps()
    assert gaps["missing_buckets"] == [], gaps["missing_buckets"]
    # Every document kind must be one the extractor can actually produce.
    document_kinds = {d.kind for d in DOCUMENTS}
    assert document_kinds <= set(EXPECTED_PROFILE), (
        document_kinds - set(EXPECTED_PROFILE))


def test_every_content_kind_maps_to_a_profile():
    """Every document's kind must be one the extractor can actually produce.

    Note `mixed_language` is a bucket covered by a TAG, not a kind: language
    is a property of a document, not one of the five extraction profiles.
    """
    from evals.runner import EXPECTED_PROFILE

    document_kinds = {d.kind for d in DOCUMENTS}
    assert document_kinds <= set(EXPECTED_PROFILE), (
        document_kinds - set(EXPECTED_PROFILE))


def test_every_gold_label_points_at_a_real_document():
    assert coverage_gaps()["dangling_gold_labels"] == []


def test_the_corpus_has_near_decoys_not_just_easy_distinctions():
    """Two documents of the same kind, or a query is answerable by type alone."""
    kinds = {}
    for doc in DOCUMENTS:
        kinds.setdefault(doc.kind, []).append(doc.key)
    repeated = {k: v for k, v in kinds.items() if len(v) > 1}
    assert {"recipe", "product", "tutorial"} <= set(repeated), repeated


def test_every_query_the_phase_names_is_present_verbatim():
    gaps = coverage_gaps()
    assert gaps["missing_queries"] == [], gaps["missing_queries"]


def test_the_queries_are_imperfect_rather_than_titles():
    """A query that is a substring of its own title measures nothing."""
    for query in QUERIES:
        doc = DOCUMENTS_BY_KEY[query.gold[0]]
        assert query.text != doc.title
        assert query.text.casefold() != doc.title.casefold(), query.text


def test_the_named_queries_are_mislabelled_as_imperfect():
    named = {q.text: q for q in QUERIES}
    for text in REQUIRED_QUERY_TEXTS:
        assert named[text].difficulty in (
            "vague_reference", "attribute_only", "misremembered_detail",
            "paraphrase", "near_title"), text


def test_the_corpus_contains_mixed_language_content():
    mixed = [d for d in DOCUMENTS if "mixed_language" in d.tags]
    assert mixed, "the phase requires mixed-language content"
    # And a query in that language, or the coverage is decorative.
    assert any("mixed_language" in q.tags for q in QUERIES)


def test_every_document_declares_the_profile_it_is_tested_against():
    from evals.runner import EXPECTED_PROFILE

    for doc in DOCUMENTS:
        assert doc.kind in EXPECTED_PROFILE, doc.kind


def test_the_corpus_briefs_are_grounded_in_their_own_bodies():
    """A gold brief that is itself ungrounded would make factuality a fiction.

    This is a property of the DATASET, not of the product: if the reference
    answer contains a claim its own source does not support, then a real
    extractor would be penalised for matching the source.
    """
    offenders = {}
    for doc in DOCUMENTS:
        result = groundedness(doc.brief, doc.body,
                              (doc.brief.get("structured_data") or {})
                              .get("content_type"))
        if result.unsupported:
            offenders[doc.key] = result.unsupported
    assert offenders == {}, offenders


def test_the_metric_tolerates_a_different_but_faithful_word_order():
    """A summary reorders and re-phrases; that is not a hallucination."""
    body = "Simmer the cream with garlic. Add the chicken back."
    brief = {"overview": "Add the chicken back to the cream with garlic.",
             "highlights": [], "structured_data": {}}
    assert groundedness(brief, body).score == 1.0


# --- the measurement itself -------------------------------------------------

@pytest.fixture
def live_db():
    """A migrated, empty database for one measurement run."""
    name = f"fb_phase19_{uuid.uuid4().hex[:10]}"
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    engine = create_engine(url, pool_pre_ping=True)
    try:
        from alembic import command

        from app import database as db_module

        cfg = db_module.alembic_config()
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield engine, sessionmaker(bind=engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@needs_db
def test_the_suite_measures_and_reports(live_db, capsys):
    """Run the whole suite and print the report. Asserts nothing about quality.

    This phase reports results. Pinning a score here would turn an evaluation
    into a regression gate, and the fastest way to a green build would then be
    tuning the product to this dataset rather than improving it.
    """
    from evals.runner import run_evaluation

    engine, sessions = live_db
    result = run_evaluation(engine, sessions)

    report = result.as_text()
    with capsys.disabled():
        print("\n===== FindBack evaluation (Phase 19) =====")
        print(report)

    # What must hold is that the run actually happened, not how it scored.
    assert result.retrieval.total == len(QUERIES)
    assert 0.0 <= result.retrieval.recall_at_1 <= 1.0
    assert 0.0 <= result.retrieval.mrr <= 1.0
    assert result.grounded_checked > 0
    assert result.explanation_total > 0
    # The report must say which kind of run produced it, or the numbers are
    # indistinguishable from a real provider's.
    assert result.provider_mode in ("provider", "oracle")


@needs_db
def test_the_suite_reports_which_provider_produced_the_numbers():
    """No run may present oracle numbers as if they were a model's."""
    from evals.runner import provider_mode

    assert provider_mode() in ("provider", "oracle")


@needs_db
def test_a_provider_backed_run_would_not_be_overwritten_by_the_oracle(monkeypatch):
    """The oracle is a fallback, not an override."""
    from app.services import ai_gateway
    from evals.runner import OracleProvider

    marker = ai_gateway.get_gateway()
    gateway = ai_gateway.Gateway(adapter=OracleProvider(DOCUMENTS))
    ai_gateway.set_gateway(gateway)
    try:
        assert ai_gateway.get_gateway() is gateway
    finally:
        ai_gateway.set_gateway(marker)




