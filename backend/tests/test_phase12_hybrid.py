"""Phase 12: hybrid memory search.

    TEST_DATABASE_URL=... python -m pytest tests/test_phase12_hybrid.py -q

Five queries the phase names, each answered by the memory the user meant, with a
reason built from terms that actually matched.
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping Phase 12 hybrid search tests",
)

# Five memories, one per query the phase names. Each is described the way a user
# would remember it, which is often NOT how it is titled.
MEMORIES = [
    {
        "key": "aws_video",
        "title": "Zero-downtime deploys, from the field",
        "overview": "A conference talk about deploying without downtime and "
                    "rolling back when a deploy goes wrong.",
        "highlights": ["blue-green deployment", "automated rollback",
                       "health checks before cutover"],
        "structured_data": {"content_type": "video",
                            "items": ["blue-green deploys", "rollback drill",
                                      "database migration safety"],
                            "speakers": ["Dana Okafor"]},
        "entities": ["AWS", "Kubernetes"], "topics": ["deploy", "recovery"],
        "intent": ["watch", "learn"],
        "body": "[00:00] Introduction. [12:30] The team runs on AWS with "
                "Kubernetes behind it, and a bad deploy must not take the "
                "service down. [12:31] Recovery is automated: the rollback "
                "trigger fires the moment health checks fail after a deploy.",
        "domain": "conf.example.test",
        "note": None,
    },
    {
        "key": "chicken_recipe",
        "title": "Creamy chicken pasta",
        "overview": "A thirty minute pasta with chicken in a cream sauce.",
        "highlights": ["Pan sear the chicken first", "Finish in the sauce"],
        "structured_data": {"content_type": "recipe",
                            "ingredients": ["chicken thighs", "double cream",
                                            "garlic", "pasta"],
                            "steps": ["Sear the chicken", "Boil the pasta",
                                      "Simmer chicken in cream"],
                            "time": "30 minutes", "temperature": ""},
        "entities": ["chicken thighs", "double cream"],
        "topics": ["italian", "dinner"], "intent": ["cook"],
        "body": "Sear the chicken thighs, boil the pasta, then simmer the "
                "chicken in double cream with garlic.",
        "domain": "recipes.example.test",
        "note": "make this for the weekend",
    },
    {
        "key": "ai_presentation",
        "title": "Deck building, revisited",
        "overview": "A talk on building presentation decks that read well.",
        "highlights": ["One idea per slide", "Cut the jargon"],
        "structured_data": {"content_type": "video",
                            "items": ["one idea per slide", "cut the jargon",
                                      "rehearse out loud"]},
        "entities": ["Pitch", "Slides"], "topics": ["presentation", "writing"],
        "intent": ["watch"],
        "body": "A presentation is not a document. Build the deck around one "
                "idea per slide.",
        "domain": "talks.example.test",
        "note": None,
    },
    {
        "key": "k8s_tutorial",
        "title": "Cluster recovery runbook",
        "overview": "Steps for recovering a Kubernetes cluster after a node "
                    "failure.",
        "highlights": ["Drain the node", "Reschedule the pods"],
        "structured_data": {"content_type": "tutorial",
                            "goal": "A healthy cluster after a node dies",
                            "prerequisites": ["kubectl access", "etcd backup"],
                            "steps": ["cordon the node", "drain the pods",
                                      "recover the workload"],
                            "tools": ["kubectl"],
                            "commands": ["kubectl cordon node-3",
                                         "kubectl drain node-3"]},
        "entities": ["Kubernetes", "etcd"], "topics": ["tutorial", "recovery"],
        "intent": ["learn"],
        "body": "Cordon the node, drain it, then let recovery reschedule. "
                "kubectl cordon node-3",
        "domain": "docs.example.test",
        "note": None,
    },
    {
        "key": "headphones",
        "title": "QuietComfort 45 review",
        "overview": "Six weeks with the QuietComfort 45 over-ear headphones.",
        "highlights": ["Deep bass", "Case is bulky"],
        "structured_data": {"content_type": "product",
                            "product_name": "QuietComfort 45",
                            "price": "$249",
                            "specifications": ["black", "30-hour battery",
                                               "over-ear"],
                            "pros": ["Excellent noise cancelling"],
                            "cons": ["Bulky case"],
                            "use_case": "Long flights"},
        "entities": ["QuietComfort 45"], "topics": ["audio", "reviews"],
        "intent": ["buy"],
        "body": "The QuietComfort 45 come in black and are over-ear. Battery "
                "lasts thirty hours.",
        "domain": "shop.example.test",
        "note": "the black pair, keep this link",
    },
]

# query -> (expected memory key, terms the reason must contain)
QUERIES = [
    ("that AWS video about fixing a deployment", "aws_video",
     ["aws", "video", "deployment"]),
    ("the chicken recipe with cream", "chicken_recipe", ["chicken", "cream"]),
    ("the AI presentation tool", "ai_presentation", ["presentation"]),
    ("that Kubernetes recovery tutorial", "k8s_tutorial",
     ["kubernetes", "recovery"]),
    ("the black headphones I saved", "headphones", ["black"]),
]


@pytest.fixture(scope="module")
def admin_engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        eng.connect().close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"cannot reach TEST_DATABASE_URL: {exc}")
    return eng


@pytest.fixture
def db(admin_engine):
    name = f"fb_phase12_{uuid.uuid4().hex[:10]}"
    admin = create_engine(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres",
                          isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    eng = create_engine(url, pool_pre_ping=True)
    try:
        from alembic import command

        from app import database as db_module

        cfg = db_module.alembic_config()
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def sessions(db):
    return sessionmaker(bind=db)


def _words(text):
    return [w.strip(".,;:()[]").lower() for w in text.split()]


def _seed(db, sessions):
    """Process all five memories through the real pipeline."""
    import app.models as models
    from app import database as db_module
    import app.tasks as app_tasks
    from app.schemas import Brief
    from app.services import embedder, extractor, fetcher, storage

    uid = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": uid, "e": "searcher@example.test"})
        s.commit()

    all_words = set()
    for memory in MEMORIES:
        all_words |= set(w.strip(".,;:()[]").lower()
                         for w in (memory["body"] + memory["title"] +
                                   memory["overview"]).split())
    vocab = sorted(w for w in all_words if w)

    # A stand-in for a real embedding model: terms are weighted by how rare they
    # are, so a distinctive word like "kubernetes" counts for far more than a
    # common one. A bag-of-words with equal weights would rank by word overlap
    # alone and would not test the fusion at all.
    document_frequency = {word: 0 for word in vocab}
    for memory in MEMORIES:
        for word in set(_words(memory["body"] + memory["title"] +
                               memory["overview"])):
            if word in document_frequency:
                document_frequency[word] += 1
    weights = {word: 1.0 / (1.0 + document_frequency[word]) for word in vocab}

    def vec(text):
        words = {w.strip(".,;:()[]").lower() for w in text.split()}
        vector = [0.0] * 1536
        for index, word in enumerate(vocab):
            if word in words:
                vector[index] = weights[word]
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    async def _embed(texts, task="document"):
        return [vec(t) for t in texts]

    async def _embed_one(text, task="document"):
        return vec(text)

    async def _extract(text, title="", url=""):
        # Keyed by URL, not by the fetched text: NORMALIZE rewrites whitespace,
        # so the body is not guaranteed to survive byte-identical.
        for memory in MEMORIES:
            if memory["key"] in (url or ""):
                return Brief(title=memory["title"], overview=memory["overview"],
                             highlights=memory["highlights"],
                             entities=memory["entities"], topics=memory["topics"],
                             intent=memory["intent"],
                             structured_data=memory["structured_data"])
        return Brief(title=title, overview=text[:100])

    embedder.embed_many = _embed
    embedder.embed_text = _embed_one
    embedder.embedding_model_name = lambda: "test-model"
    storage.store_raw_snapshot = lambda i, p: None

    async def _fetch(url, preview=""):
        for memory in MEMORIES:
            if memory["key"] in url:
                return {"text": memory["body"], "title": memory["title"],
                        "source_type": "web"}
        return {"text": "", "title": ""}
    fetcher.fetch_content = _fetch
    # Replaced on the module: `restore_service_modules` in conftest.py puts the
    # real one back after this test, so this cannot leak into another one.
    extractor.extract_brief = _extract

    ids = {}
    previous = db_module._engine
    db_module._engine = db
    db_module._sessionmaker = None
    try:
        for memory in MEMORIES:
            with sessions() as s:
                asset = models.ContentAsset(
                    canonical_url=f"https://{memory['domain']}/{memory['key']}",
                    dedupe_key=f"url:https://{memory['domain']}/{memory['key']}",
                    owner_user_id=uid, visibility="UNKNOWN")
                s.add(asset)
                s.flush()
                # Phase 16: items_user_content_fk requires this row to exist
                # before an item may name the asset. Real saves always create it,
                # so the fixture creates it too. Setup only.
                s.add(models.UserMemory(user_id=uid, content_id=asset.id))
                s.flush()
                item = models.Item(
                    user_id=uid, url=asset.canonical_url,
                    canonical_url=asset.canonical_url,
                    title=memory["title"], content_id=asset.id,
                    source_domain=memory["domain"])
                s.add(item)
                s.flush()
                item_id = str(item.id)
                s.commit()
            app_tasks.process_item.apply(args=(item_id,), throw=False)
            ids[memory["key"]] = item_id
            if memory["note"]:
                with sessions() as s:
                    # Phase 16: the memory row already exists (created above so
                    # items_user_content_fk is satisfied), so the note is an
                    # UPDATE. Same value the INSERT used, same user, same
                    # content -- no assertion changes.
                    s.execute(text("""
                        UPDATE user_memories SET user_note = :n
                        WHERE user_id = :u AND content_id = (
                            SELECT id FROM content_assets
                            WHERE canonical_url = :url)
                    """), {"u": uid, "n": memory["note"],
                           "url": f"https://{memory['domain']}/{memory['key']}"})
                    s.commit()
    finally:
        db_module._engine = previous
        db_module._sessionmaker = None
    return uid, ids


def _search(db, uid, query, **kwargs):
    import asyncio

    from app.services import search

    with Session(bind=db) as s:
        return asyncio.run(search.hybrid_search(s, uid, query, limit=10, **kwargs))


@pytest.mark.parametrize("path", ["chunk_vector", "chunk_lexical", "note"])
def test_category_filters_each_candidate_path_before_limit(db, sessions, path):
    import asyncio
    from app.services import search

    uid, ids = _seed(db, sessions)
    with sessions() as s:
        if path == "chunk_vector":
            rows = asyncio.run(search.chunk_search(
                s, uid, "headphones", limit=1, category="recipe"))
        elif path == "chunk_lexical":
            s.execute(text("UPDATE chunks SET chunk_text = 'needle'"))
            s.commit()
            rows = search.chunk_lexical_search(
                s, uid, "needle", ["needle"], limit=1, category="recipe")
        else:
            s.execute(text("UPDATE user_memories SET user_note = 'needle'"))
            s.commit()
            rows = search.note_search(s, uid, ["needle"], limit=1, category="recipe")
    assert [str(row[0]) for row in rows] == [ids["chicken_recipe"]]
    assert all(row[4] == "recipe" for row in rows)


def test_search_endpoint_excludes_other_categories(db, sessions):
    import asyncio
    import httpx
    from app.main import app
    from app.database import get_db
    from app.auth import get_current_user
    from types import SimpleNamespace

    uid, ids = _seed(db, sessions)
    def get_session():
        with sessions() as s:
            yield s
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    try:
        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                return await client.get("/api/v1/search", params={
                    "q": "headphones", "category": "recipe"})
        response = asyncio.run(request())
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert response.status_code == 200
    rows = response.json()["results"]
    # A category restricts matches; it cannot turn a recipe into headphones.
    assert rows == []
    assert all(row["category"] == "recipe" for row in rows)


def test_recent_category_filter_and_pagination(db, sessions):
    import asyncio
    import httpx
    from types import SimpleNamespace
    from app.main import app
    from app.database import get_db
    from app.auth import get_current_user

    uid, ids = _seed(db, sessions)
    with sessions() as s:
        s.execute(text("UPDATE items SET category='recipe' WHERE id=:id"),
                  {"id": ids["headphones"]})
        s.commit()
    def get_session():
        with sessions() as s:
            yield s
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = get_session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid)
    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            first = await client.get("/api/v1/items", params={"category":"recipe", "limit":1})
            assert first.status_code == 200
            page = first.json()
            assert page["items"][0]["category"] == "recipe"
            assert page["next_cursor"]
            second = await client.get("/api/v1/items", params={
                "category":"recipe", "limit":1, "cursor":page["next_cursor"]})
            assert second.status_code == 200
            assert second.json()["items"][0]["category"] == "recipe"
            assert second.json()["next_cursor"] is None
            assert {page["items"][0]["id"], second.json()["items"][0]["id"]} == {
                ids["chicken_recipe"], ids["headphones"]}
            invalid = await client.get("/api/v1/items", params={"category":"unknown"})
            assert invalid.status_code == 422
            unfiltered = await client.get("/api/v1/items")
            assert len(unfiltered.json()["items"]) == 5
    try:
        asyncio.run(requests())
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("query,expected_key,terms", QUERIES,
                         ids=[q for q, _, _ in QUERIES])
def test_the_query_finds_the_memory_it_names(db, sessions, query,
                                             expected_key, terms):
    """The phase's five queries, each answered by the memory meant."""
    uid, ids = _seed(db, sessions)
    results, _took = _search(db, uid, query)

    ranked = [str(r["row"][0]) for r in results]
    assert ranked, f"{query!r} returned nothing"
    assert ranked[0] == ids[expected_key], (
        f"{query!r} returned {[str(r['row'][1]) for r in results]}, "
        f"expected the {expected_key} memory")


@pytest.mark.parametrize("query,expected_key,terms", QUERIES,
                         ids=[q for q, _, _ in QUERIES])
def test_the_reason_names_the_terms_that_matched(db, sessions, query,
                                                 expected_key, terms):
    """The reason is built from real matches, not from the category."""
    uid, ids = _seed(db, sessions)
    results, _took = _search(db, uid, query)
    top = results[0]
    reason = top["match_reason"]

    assert not reason.startswith("Matched: "), \
        "a category is not a reason for a match"
    for term in terms:
        assert term in reason.lower(), f"{term!r} missing from reason {reason!r}"


def test_a_word_held_only_in_structured_data_is_findable(db, sessions):
    """`cream` is in the recipe's ingredients, not in its title or summary.

    This is the gap Phase 12 exists to close: before it this query had nothing
    to match lexically, because the word lived only in structured_data.
    """
    uid, ids = _seed(db, sessions)
    with db.connect() as conn:
        doc = conn.execute(text("SELECT search_text FROM items WHERE id = :i"),
                           {"i": ids["chicken_recipe"]}).scalar()
    assert "double cream" in doc, "structured_data must reach search_text"

    results, _took = _search(db, uid, "cream")
    assert str(results[0]["row"][0]) == ids["chicken_recipe"]


def test_topics_reach_the_search_document(db, sessions):
    """A topic is as searchable as a title: `recovery` is a topic here."""
    uid, ids = _seed(db, sessions)
    with db.connect() as conn:
        doc = conn.execute(text("SELECT search_text FROM items WHERE id = :i"),
                           {"i": ids["aws_video"]}).scalar()
    assert "recovery" in doc, doc

    results, _took = _search(db, uid, "how does the recovery work")
    top = next(r for r in results if str(r["row"][0]) == ids["aws_video"])
    assert "recovery" in top["match_reason"], top["match_reason"]


def test_a_user_note_can_be_the_reason(db, sessions):
    """The user's own words count as evidence -- "the weekend" is their memory."""
    from app.services import search

    terms = search.query_terms("weekend")
    row = ("id", "Creamy chicken pasta", "summary", [], "recipe",
           "recipes.example.test", None, None, "content")
    assert search.evidence_reason(terms, row, note="make this for the weekend") \
        == "weekend (your note)"


def test_metadata_filter_narrows_every_candidate_list(db, sessions):
    """A filter must exclude a memory, not just demote it."""
    from datetime import datetime, timedelta, timezone

    uid, ids = _seed(db, sessions)
    results, _took = _search(db, uid, "chicken",
                             source_domain="recipes.example.test")
    assert results, "the filter should keep the matching memory"
    assert all(r["row"][5] == "recipes.example.test" for r in results)

    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    results, _took = _search(db, uid, "chicken", saved_after=tomorrow)
    assert results == [], "a future-only filter must return nothing"


def test_search_never_crosses_a_user_boundary(db, sessions):
    """Phase 4's rule, still true of every new candidate list."""
    uid, _ids = _seed(db, sessions)
    other = uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  {"i": other, "e": "stranger@example.test"})
        s.commit()

    results, _took = _search(db, other, "aws kubernetes deployment chicken")
    assert results == [], "another user's memories must never appear"


# --- the user's own note is a source of evidence (H7) ---------------------

def test_a_query_that_only_matches_the_note_finds_the_memory(db, sessions):
    """"weekend" is in exactly one place in this corpus: the note.

    It appears in no title, summary, topic, highlight, structured value or
    body, so the only way this query can answer with the memory is by searching
    the note. Before this, the note could only explain a match that some other
    list had already produced, and a query built from the user's own words about
    why they saved something returned nothing.
    """
    uid, ids = _seed(db, sessions)

    results, _took = _search(db, uid, "weekend")
    assert results, "the note alone must make the memory a candidate"
    assert str(results[0]["row"][0]) == ids["chicken_recipe"]
    assert "note" in (results[0]["match_reason"] or ""), \
        results[0]["match_reason"]


def test_the_note_is_never_copied_onto_the_shared_content(db, sessions):
    """It stays on user_memories: not on the asset, not in the search document.

    The note is the user's own words about someone else's content. Copying it
    onto the shared ContentAsset, or folding it into the item's indexed text or
    embedding input, would hand it to whoever searches next.
    """
    uid, _ids = _seed(db, sessions)

    with db.connect() as conn:
        assets = conn.execute(text(
            "SELECT brief, title, structured_data::text, entities::text, "
            "topics::text FROM content_assets")).all()
        asset_blob = " ".join(str(value) for row in assets for value in row)
        documents = conn.execute(text(
            "SELECT coalesce(search_text, '') FROM items")).scalars().all()
        columns = conn.execute(text(
            "SELECT coalesce(title_clean, '') || ' ' || coalesce(summary, '') "
            "|| ' ' || coalesce(raw_text, '') FROM items")).scalars().all()

    assert "weekend" not in asset_blob, "the note reached the shared asset"
    assert all("weekend" not in d for d in documents), \
        "the note reached the item's search document"
    assert all("weekend" not in c for c in columns), \
        "the note reached the item's own columns"


def test_another_users_note_is_never_a_candidate(db, sessions):
    """Two users, one shared PUBLIC asset, one note between them.

    The author must find their own note; the other user must get nothing at
    all -- the note is not merely ranked lower, it is absent.
    """
    from app import models

    author, viewer = uuid.uuid4(), uuid.uuid4()
    with sessions() as s:
        s.execute(text("INSERT INTO users (id, email) VALUES (:i, :e)"),
                  [{"i": str(author), "e": "author@example.test"},
                   {"i": str(viewer), "e": "viewer@example.test"}])
        s.commit()

    item_ids = {}
    with sessions() as s:
        asset = models.ContentAsset(
            canonical_url="https://shared.example.test/thing",
            dedupe_key="url:https://shared.example.test/thing",
            visibility=models.VISIBILITY_PUBLIC,
            title="A shared thing", brief="something both users saved")
        s.add(asset)
        s.flush()
        for user, note in ((author, "zanzibar packing list"), (viewer, None)):
            # The composite FK requires the memory before the item.
            s.add(models.UserMemory(user_id=user, content_id=asset.id,
                                    user_note=note))
            s.flush()
            item = models.Item(
                user_id=user, url=asset.canonical_url,
                canonical_url=asset.canonical_url, title="A shared thing",
                content_id=asset.id, status="ready",
                search_text="a shared thing",
                source_domain="shared.example.test")
            s.add(item)
            s.flush()
            item_ids[user] = str(item.id)
        s.commit()

    mine, _took = _search(db, author, "zanzibar")
    assert mine, "sanity: the author's own note is findable"
    assert str(mine[0]["row"][0]) == item_ids[author]

    theirs, _took = _search(db, viewer, "zanzibar")
    assert theirs == [], "another user's note must never be a candidate"
