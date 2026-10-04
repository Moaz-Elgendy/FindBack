"""The FindBack search/AI evaluation corpus (Phase 19).

A labelled corpus plus the queries a real user would actually type, which is
the point: the queries are deliberately imperfect. Nobody searches for
"Zero-downtime deploys, from the field"; they search for "that AWS deployment
recovery thing".

Facts inside each `brief` are drawn verbatim from the document `body`. That is
what makes factuality checkable without a judge model: a brief statement that
does not appear in its source is ungrounded by definition.

Nothing here reaches the network and no credential is required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Document:
    """One labelled piece of saved content."""

    key: str
    kind: str
    source_type: str
    title: str
    body: str
    brief: dict[str, Any]
    # Which required content buckets this document represents.
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Query:
    """One imperfect query and the document the user actually meant.

    `gold` holds more than one key only when two documents are genuinely
    interchangeable answers.
    """

    text: str
    gold: tuple[str, ...]
    difficulty: str = ""
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


# --- the corpus -------------------------------------------------------------
# Eight content buckets: video, article, post, recipe, product, tutorial, AI
# tool, mixed-language. Several are near-decoys for each other on purpose --
# two recipes, two products, two tutorials -- so that returning the right TYPE
# of content is not enough to score well.

DOCUMENTS: tuple[Document, ...] = (
    Document(
        key="claude_skills_video",
        kind="video",
        source_type="video",
        title="5 Claude Skills That Actually Save Me Hours",
        body=(
            "[00:00] Today I want to walk through five skills I set up for "
            "Claude and none of them are demos. [02:10] The first one writes "
            "test files from a diff and it has saved me the most time of any of "
            "them. [05:40] The second explains a stack trace in plain language "
            "so a new person on the team can read it. [09:30] The third drafts "
            "the changelog from the commits. [12:15] The fourth finds the "
            "flaky test that has been failing for three weeks. [16:00] The fifth "
            "writes the database migration when I describe the change in a "
            "sentence. All five live in one repository and cost nothing."
        ),
        brief={
            "title": "5 Claude Skills That Actually Save Me Hours",
            "overview": "A walkthrough of five Claude skills the author set up.",
            "highlights": [
                "writes test files from a diff",
                "explains a stack trace in plain language",
                "drafts the changelog from the commits",
                "finds the flaky test",
                "writes the database migration",
            ],
            "structured_data": {
                "content_type": "list",
                "items": [
                    "writes test files from a diff",
                    "explains a stack trace in plain language",
                    "drafts the changelog from the commits",
                    "finds the flaky test",
                    "writes the database migration",
                ],
            },
            "entities": ["Claude"],
            "topics": ["automation", "developer tools"],
            "intent": ["watch", "learn"],
            "actions": [],
            "timestamps": ["00:00", "02:10", "05:40", "09:30", "12:15", "16:00"],
        },
        tags=("video", "ai_tools"),
    ),
    Document(
        key="aws_deploy_talk",
        kind="video",
        source_type="video",
        title="Zero-downtime deploys, from the field",
        body=(
            "[00:00] This is a conference talk about shipping without an "
            "outage. [04:20] We run on AWS with Kubernetes behind a load "
            "balancer, so a bad deploy must not take the service down. "
            "[12:30] Recovery is automated: the rollback trigger fires the "
            "moment the health checks fail after a deploy. [12:31] That is the "
            "whole recovery story, and it took four incidents to learn it. "
            "[21:00] We also rehearse the rollback drill every month."
        ),
        brief={
            "title": "Zero-downtime deploys, from the field",
            "overview": "A talk about deploying without downtime on AWS.",
            "highlights": [
                "a bad deploy must not take the service down",
                "the rollback trigger fires when health checks fail",
                "rehearse the rollback drill every month",
            ],
            "structured_data": {
                "content_type": "list",
                "items": [
                    "a bad deploy must not take the service down",
                    "the rollback trigger fires when health checks fail",
                    "rehearse the rollback drill every month",
                ],
            },
            "entities": ["AWS", "Kubernetes"],
            "topics": ["deployment", "recovery"],
            "intent": ["watch", "learn"],
            "actions": ["rehearse the rollback drill every month"],
            "timestamps": ["00:00", "04:20", "12:30", "21:00"],
        },
        tags=("video",),
    ),
    Document(
        key="chicken_cream_recipe",
        kind="recipe",
        source_type="recipe",
        title="Creamy chicken pasta, weeknight version",
        body=(
            "Serves four. You need chicken thighs, double cream, garlic and "
            "pasta. Sear the chicken thighs until they are brown, then set them "
            "aside. Boil the pasta in salted water. Simmer the cream with "
            "crushed garlic for two minutes, add the chicken back, and pour the "
            "sauce over the pasta. It takes about thirty minutes end to end and "
            "there is no oven involved."
        ),
        brief={
            "title": "Creamy chicken pasta, weeknight version",
            "overview": "A thirty minute creamy chicken pasta for four.",
            "highlights": [
                "sear the chicken thighs until brown",
                "simmer the cream with garlic for two minutes",
                "pour the sauce over the pasta",
            ],
            "structured_data": {
                "content_type": "recipe",
                "ingredients": ["chicken thighs", "double cream", "garlic",
                                "pasta"],
                "steps": [
                    "sear the chicken thighs",
                    "boil the pasta",
                    "simmer the cream with garlic",
                    "pour the sauce over the pasta",
                ],
                "time": "30 minutes",
                "temperature": "",
            },
            "entities": ["chicken thighs", "double cream"],
            "topics": ["italian", "dinner"],
            "intent": ["cook"],
            "actions": ["sear the chicken thighs", "boil the pasta"],
            "timestamps": [],
        },
        tags=("recipe",),
    ),
    Document(
        key="lentil_soup_recipe",
        kind="recipe",
        source_type="recipe",
        title="Lentil soup that freezes well",
        body=(
            "Red lentils, a stock cube, a carrot and a bay leaf. Simmer "
            "everything for twenty five minutes and blend it. This one freezes "
            "in portions and reheats without losing the texture, which is the "
            "only reason I make a double batch."
        ),
        brief={
            "title": "Lentil soup that freezes well",
            "overview": "A lentil soup that freezes in portions.",
            "highlights": ["simmer for twenty five minutes",
                           "freeze in portions"],
            "structured_data": {
                "content_type": "recipe",
                "ingredients": ["red lentils", "stock cube", "carrot",
                                "bay leaf"],
                "steps": ["simmer everything", "blend", "freeze in portions"],
                "time": "25 minutes",
                "temperature": "",
            },
            "entities": ["red lentils"],
            "topics": ["soup", "batch cooking"],
            "intent": ["cook"],
            "actions": [],
            "timestamps": [],
        },
        tags=("recipe",),
    ),
    Document(
        key="headphones_product",
        kind="product",
        source_type="product",
        title="QuietComfort 45 review",
        body=(
            "The QuietComfort 45 come in black and are over ear. They cost two "
            "hundred and forty nine dollars. The battery lasts thirty hours and "
            "the case is genuinely bulky. The noise cancelling is the best in "
            "the class, and they are comfortable for a long flight."
        ),
        brief={
            "title": "QuietComfort 45 review",
            "overview": "A review of the QuietComfort 45 over ear headphones.",
            "highlights": [
                "the noise cancelling is the best in the class",
                "the battery lasts thirty hours",
                "the case is genuinely bulky",
            ],
            "structured_data": {
                "content_type": "product",
                "product_name": "QuietComfort 45",
                "price": "$249",
                "specifications": ["black", "over ear", "thirty hours"],
                "pros": ["best in class noise cancelling",
                         "comfortable for a long flight"],
                "cons": ["the case is genuinely bulky"],
                "use_case": "long flights",
            },
            "entities": ["QuietComfort 45"],
            "topics": ["audio", "headphones"],
            "intent": ["buy"],
            "actions": [],
            "timestamps": [],
        },
        tags=("product",),
    ),
    Document(
        key="keyboard_product",
        kind="product",
        source_type="product",
        title="A cheap mechanical keyboard I kept",
        body=(
            "Sixty five dollars for a mechanical keyboard with tactile switches. "
            "The case is plastic, the stabilisers need a week to bed in, and "
            "once they are in it is the best keyboard I have owned. It is a "
            "white board, not black."
        ),
        brief={
            "title": "A cheap mechanical keyboard I kept",
            "overview": "A review of a sixty five dollar mechanical keyboard.",
            "highlights": ["the stabilisers need a week to bed in",
                           "tactile switches"],
            "structured_data": {
                "content_type": "product",
                "product_name": "mechanical keyboard with tactile switches",
                "price": "$65",
                "specifications": ["tactile switches", "plastic case", "white"],
                "pros": ["best keyboard owned"],
                "cons": ["stabilisers need a week to bed in"],
                "use_case": "daily typing",
            },
            "entities": [],
            "topics": ["keyboards", "desks"],
            "intent": ["buy"],
            "actions": [],
            "timestamps": [],
        },
        tags=("product",),
    ),
    Document(
        key="k8s_tutorial",
        kind="tutorial",
        source_type="tutorial",
        title="Recovering a Kubernetes node after it dies",
        body=(
            "Goal: a healthy cluster after a node fails. You need kubectl access "
            "and an etcd backup. Cordon the node with kubectl cordon node-3, "
            "drain it so the pods move, then let the scheduler recover the "
            "workload. Verify with the nodes screen. The whole procedure takes "
            "about ten minutes and the backup is what saves you."
        ),
        brief={
            "title": "Recovering a Kubernetes node after it dies",
            "overview": "Cordon the node, drain it, and recover the workload.",
            "highlights": ["cordon the node", "drain the pods",
                           "let the scheduler recover the workload"],
            "structured_data": {
                "content_type": "tutorial",
                "goal": "a healthy cluster after a node fails",
                "prerequisites": ["kubectl access", "an etcd backup"],
                "steps": ["cordon the node", "drain it", "recover the workload"],
                "tools": ["kubectl"],
                "commands": ["kubectl cordon node-3", "kubectl drain node-3"],
            },
            "entities": ["Kubernetes", "etcd", "kubectl"],
            "topics": ["kubernetes", "recovery"],
            "intent": ["learn"],
            "actions": ["cordon the node", "drain it"],
            "timestamps": [],
        },
        tags=("tutorial",),
    ),
    Document(
        key="pg_replication_tutorial",
        kind="tutorial",
        source_type="tutorial",
        title="Postgres logical replication in five steps",
        body=(
            "Goal: logical replication between two Postgres servers. Set "
            "wal_level to logical and restart. Create a publication on the "
            "primary, create a subscription on the replica, and check "
            "pg_stat_replication. Version sixteen is assumed."
        ),
        brief={
            "title": "Postgres logical replication in five steps",
            "overview": "Setting up logical replication between two servers.",
            "highlights": ["set wal_level to logical", "create a publication",
                           "check pg_stat_replication"],
            "structured_data": {
                "content_type": "tutorial",
                "goal": "logical replication between two Postgres servers",
                "prerequisites": ["Postgres 16", "restart access"],
                "steps": ["set wal_level to logical", "restart",
                          "create a publication", "create a subscription"],
                "tools": ["psql"],
                "commands": ["ALTER SYSTEM SET wal_level = 'logical'"],
            },
            "entities": ["Postgres", "pg_stat_replication"],
            "topics": ["databases", "replication"],
            "intent": ["learn"],
            "actions": ["set wal_level to logical"],
            "timestamps": [],
        },
        tags=("tutorial",),
    ),
    Document(
        key="deck_tool",
        kind="ai_tool",
        source_type="tool",
        title="Deckly, and what it is actually for",
        body=(
            "Deckly is an AI tool for making presentations. You give it an "
            "outline and it drafts the slides, then you cut the jargon out "
            "yourself. It is good at a first draft and bad at anything you "
            "have to present to a board."
        ),
        brief={
            "title": "Deckly, and what it is actually for",
            "overview": "Deckly is an AI tool for making presentations.",
            "highlights": ["give it an outline and it drafts the slides",
                           "good at a first draft"],
            "structured_data": {
                "content_type": "general",
                "capabilities": ["draft slides from an outline"],
                "limitations": ["nothing you present to a board"],
            },
            "entities": ["Deckly"],
            "topics": ["presentations", "ai tools"],
            "intent": ["learn"],
            "actions": [],
            "timestamps": [],
        },
        tags=("ai_tool",),
    ),
    Document(
        key="translation_tool",
        kind="ai_tool",
        source_type="tool",
        title="Lingua, a translation model you can run locally",
        body=(
            "Lingua is an AI tool for translation that runs on your own machine "
            "and never sends a line to anyone else. It handles about thirty "
            "languages well and the rest badly. The documentation is in German "
            "and English only."
        ),
        brief={
            "title": "Lingua, a translation model you can run locally",
            "overview": "Lingua runs translation on your own machine.",
            "highlights": ["runs on your own machine",
                           "handles about thirty languages well"],
            "structured_data": {
                "content_type": "general",
                "capabilities": ["local translation", "about thirty languages"],
                "limitations": ["documentation only in German and English"],
            },
            "entities": ["Lingua"],
            "topics": ["translation", "privacy"],
            "intent": ["learn"],
            "actions": [],
            "timestamps": [],
        },
        tags=("ai_tool", "mixed_language"),
    ),
    Document(
        key="search_rewrite_article",
        kind="article",
        source_type="article",
        title="Why we replaced keyword search with hybrid retrieval",
        body=(
            "Keyword search failed on paraphrase: people wrote the question "
            "rather than the words in the article. We moved to hybrid retrieval "
            "and recall improved. The latency cost was acceptable and the "
            "lexical half still earns its place for rare exact terms."
        ),
        brief={
            "title": "Why we replaced keyword search with hybrid retrieval",
            "overview": "Keyword search failed on paraphrase, so we moved to "
                        "hybrid retrieval.",
            "highlights": ["keyword search failed on paraphrase",
                           "recall improved",
                           "the lexical half still earns its place"],
            "structured_data": {"content_type": "general"},
            "entities": ["hybrid retrieval"],
            "topics": ["search", "engineering"],
            "intent": ["read", "learn"],
            "actions": [],
            "timestamps": [],
        },
        tags=("article",),
    ),
    Document(
        key="indie_post",
        kind="post",
        source_type="post",
        title="Shipped the thing",
        body=(
            "Six weeks of evenings and the feature finally shipped. The post is "
            "mostly about the deploy that broke it and the twenty minutes spent "
            "rolling it back, which felt like a failure at the time and was "
            "actually the process working."
        ),
        brief={
            "title": "Shipped the thing",
            "overview": "A short post about shipping a feature after six weeks.",
            "highlights": ["the deploy broke it", "twenty minutes rolling back"],
            "structured_data": {"content_type": "general"},
            "entities": [],
            "topics": ["shipping", "process"],
            "intent": ["read"],
            "actions": [],
            "timestamps": [],
        },
        tags=("post",),
    ),
    Document(
        key="japanese_post",
        kind="post",
        source_type="post",
        title="日本語のCharlelesについて",
        body=(
            "これは静的サイトジェネレータについての記事です。設定はSETTINGS.mdにあります。ビルドは速いです。Fm と Mdx に対応しています。"
        ),
        brief={
            "title": "日本語のCharlelesについて",
            "overview": "これは静的サイトジェネレータについての記事です。設定はSETTINGS.mdにあります。",
            "highlights": ["静的サイトジェネレータ", "ビルドは速い"],
            "structured_data": {"content_type": "general"},
            "entities": [],
            "topics": ["静的サイト", "ビルド"],
            "intent": ["read"],
            "actions": [],
            "timestamps": [],
        },
        tags=("post", "mixed_language"),
    ),
)

DOCUMENTS_BY_KEY = {doc.key: doc for doc in DOCUMENTS}


# --- the queries ------------------------------------------------------------
# Imperfect on purpose. Each one is phrased the way someone actually remembers
# a save: with a pronoun, a vague noun, or an attribute rather than the title.
# The five the phase names are marked; the rest widen the coverage.

QUERIES: tuple[Query, ...] = (
    Query("that video about Claude skills", ("claude_skills_video",),
          "vague_reference", "named by the phase",
          ("video", "ai_tools")),
    Query("the mushroom chicken recipe", ("chicken_cream_recipe",),
          "misremembered_detail",
          "named by the phase; 'mushroom' is a false detail",
          ("recipe",)),
    Query("AI tool for making presentations", ("deck_tool",),
          "attribute_only", "named by the phase", ("ai_tool",)),
    Query("that AWS deployment recovery thing", ("aws_deploy_talk",),
          "vague_reference", "named by the phase", ("video",)),
    Query("the headphones I was looking at", ("headphones_product",),
          "vague_reference", "named by the phase", ("product",)),

    Query("that kubernetes recovery tutorial", ("k8s_tutorial",),
          "vague_reference", "caps, not a keyword", ("tutorial",)),
    Query("claude skills video", ("claude_skills_video",),
          "near_title", "a more literal phrasing of the same want",
          ("video", "ai_tools")),
    Query("rollback when a deploy breaks", ("aws_deploy_talk",),
          "paraphrase", "the concept, not the words", ("video",)),
    Query("pasta with chicken in a cream sauce", ("chicken_cream_recipe",),
          "paraphrase", "describes the dish", ("recipe",)),
    Query("something to slice onions fast", ("keyboard_product",),
          "distractor", "'slice' has nothing to do with this memory"),
    Query("sous vide", ("lentil_soup_recipe",),
          "unanswerable", "nothing in the corpus is sous vide"),
    Query("postgres replication between two servers",
          ("pg_replication_tutorial",), "near_title", "a literal phrasing",
          ("tutorial",)),
    Query("a tool that runs translation on my own machine",
          ("translation_tool",), "paraphrase", "says the capability",
          ("ai_tool", "mixed_language")),
    Query("静的サイトのビルドが速いツール", ("japanese_post",),
          "mixed_language", "a query in the content's own language",
          ("mixed_language",)),
    Query("search paraphrase problem", ("search_rewrite_article",),
          "paraphrase", "one of the article's own words", ("article",)),
    Query("rolled back the deploy and felt fine about it", ("indie_post",),
          "paraphrase", "the post's actual subject", ("post",)),
    Query("over ear noise cancelling", ("headphones_product",),
          "attribute_only", "no title words at all", ("product",)),
    Query("mechanical keyboard", ("keyboard_product",),
          "near_title", "the decoy for the headphones query",
          ("product",)),
)

QUERIES_BY_TEXT = {query.text: query for query in QUERIES}

# The content buckets the phase requires the corpus to represent.
REQUIRED_BUCKETS = ("video", "article", "post", "recipe", "product",
                    "tutorial", "ai_tool", "mixed_language")

# The queries the phase names, verbatim.
REQUIRED_QUERY_TEXTS = (
    "that video about Claude skills",
    "the mushroom chicken recipe",
    "AI tool for making presentations",
    "that AWS deployment recovery thing",
    "the headphones I was looking at",
)


def coverage_gaps() -> dict[str, list[str]]:
    """What the corpus does not represent. Empty means the phase is covered.

    A bucket counts as covered if it appears either as a document `kind` (the
    profile the content should extract as) or as a `tag` (what kind of thing it
    is). "article" is not one of the five profiles, so it is a tag, not a kind.
    """
    present_tags = {tag for doc in DOCUMENTS for tag in doc.tags}
    present_kinds = {doc.kind for doc in DOCUMENTS}
    missing_queries = [q for q in REQUIRED_QUERY_TEXTS if q not in QUERIES_BY_TEXT]
    # Every gold label must point at a document that exists, or the metric is
    # measuring a label error rather than the search.
    dangling = sorted({key for q in QUERIES for key in q.gold
                       if key not in DOCUMENTS_BY_KEY})
    return {
        "missing_buckets": [b for b in REQUIRED_BUCKETS
                            if b not in present_tags | present_kinds],
        "missing_kinds": [k for k in REQUIRED_BUCKETS if k not in present_kinds],
        "missing_queries": missing_queries,
        "dangling_gold_labels": dangling,
    }



