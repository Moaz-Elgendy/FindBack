"""Phase 9: the FindBack Brief schema.

    TEST_DATABASE_URL=... python -m pytest tests/test_phase9_brief.py -q

The brief replaces the generic "summary + 3 bullets" shape. Two things matter
here: a brief for real content types validates against the schema, and a brief
with an invented top-level field is rejected.
"""
import pytest
from pydantic import ValidationError

from app.schemas import Brief
from app.services import extractor

# A "5 skills" video. The old schema cut this to 3 bullets, which loses two
# of the five things the user saved it for.
SKILLS_BRIEF = {
    "title": "5 Claude Skills That Save Me Hours",
    "overview": "A walkthrough of five Claude skills and when each is worth using.",
    "highlights": [
        "Skill 1: writes test files from a diff",
        "Skill 2: explains a stack trace in plain language",
        "Skill 3: drafts the changelog from commits",
        "Skill 4: finds the flaky test",
        "Skill 5: writes the migration",
    ],
    "structured_data": {"content_type": "video",
                        "skills": [{"name": "test-writer", "at": "01:12"}]},
    "entities": ["Claude", "Anthropic"],
    "topics": ["claude", "automation", "developer tools"],
    "intent": ["watch", "learn"],
    "actions": ["Install the skills from the linked repo"],
    "timestamps": ["00:00", "01:12", "04:30"],
}

RECIPE_BRIEF = {
    "title": "Mushroom Risotto",
    "overview": "A creamy mushroom risotto that takes about 40 minutes.",
    "highlights": ["Saute mushrooms first", "Add rice gradually",
                   "Finish with cold butter off the heat"],
    "structured_data": {"content_type": "recipe", "servings": 4,
                        "cook_minutes": 40},
    "entities": ["arborio rice", "chestnut mushrooms", "parmesan"],
    "topics": ["italian", "dinner"],
    "intent": ["cook"],
    "actions": ["Dice the mushrooms", "Toast the rice",
                "Add stock a ladle at a time"],
    "timestamps": [],
}

PRODUCT_BRIEF = {
    "title": "Sony WH-1000XM5 Headphones Review",
    "overview": "A long-term review of Sony's flagship noise cancelling headphones.",
    "highlights": ["Excellent noise cancelling", "30-hour battery",
                   "Folds flat but the case is bulky"],
    "structured_data": {"content_type": "product", "price_usd": 349,
                        "brand": "Sony"},
    "entities": ["Sony", "WH-1000XM5"],
    "topics": ["audio", "headphones", "reviews"],
    "intent": ["buy"],
    "actions": ["Compare against the XM4 price"],
    "timestamps": [],
}

TUTORIAL_BRIEF = {
    "title": "Set Up Postgres Logical Replication",
    "overview": "Step-by-step setup of logical replication between two Postgres servers.",
    "highlights": ["Enable wal_level=logical", "Create the replication user",
                   "Add the publication on the primary",
                   "Start the subscriber", "Verify with pg_stat_replication"],
    "structured_data": {"content_type": "tutorial", "tool": "Postgres",
                        "version": "16"},
    "entities": ["Postgres", "pg_stat_replication"],
    "topics": ["databases", "replication", "devops"],
    "intent": ["learn"],
    "actions": ["Set wal_level and restart", "Run CREATE SUBSCRIPTION"],
    "timestamps": [],
}

GENERAL_BRIEF = {
    "title": "Why We Rewrote Our Search",
    "overview": "An engineering write-up on replacing keyword search with hybrid retrieval.",
    "highlights": ["Keyword search missed paraphrases",
                   "Hybrid ranking improved recall",
                   "Latency cost was acceptable"],
    "structured_data": {"content_type": "article"},
    "entities": ["Elasticsearch", "BM25"],
    "topics": ["search", "engineering"],
    "intent": ["read", "learn"],
    "actions": [],
    "timestamps": [],
}

VALID_BRIEFS = [
    ("five_skills_list", SKILLS_BRIEF),
    ("recipe", RECIPE_BRIEF),
    ("product", PRODUCT_BRIEF),
    ("tutorial", TUTORIAL_BRIEF),
    ("general_content", GENERAL_BRIEF),
]


@pytest.mark.parametrize("name,payload", VALID_BRIEFS, ids=[n for n, _ in VALID_BRIEFS])
def test_a_valid_brief_is_accepted(name, payload):
    brief = extractor.brief_from_model(payload)
    assert isinstance(brief, Brief)
    assert brief.overview == payload["overview"]
    assert brief.highlights == payload["highlights"]


def test_the_schema_has_exactly_the_nine_named_fields():
    assert set(Brief.model_fields) == {
        "title", "overview", "highlights", "structured_data",
        "entities", "topics", "intent", "actions", "timestamps",
    }


def test_a_five_item_list_is_not_cut_to_three():
    """The reason the old summary+3 shape is gone."""
    brief = extractor.brief_from_model(SKILLS_BRIEF)
    assert len(brief.highlights) == 5
    memory = extractor.memory_from_brief(brief)
    assert memory.key_points == brief.highlights, \
        "the stored points must be the highlights, not a truncated copy"


@pytest.mark.parametrize("name,payload", VALID_BRIEFS, ids=[n for n, _ in VALID_BRIEFS])
def test_structured_data_stays_free_form(name, payload):
    """structured_data is not flattened or typed, so values survive verbatim."""
    brief = extractor.brief_from_model(payload)
    assert brief.structured_data == payload["structured_data"]


def test_a_new_content_type_needs_no_migration():
    """An unfamiliar content_type and nested shape still validates."""
    payload = dict(GENERAL_BRIEF, structured_data={
        "content_type": "conference_talk",
        "speakers": [{"name": "A", "talk": "x", "slides": ["a.pdf"]}],
        "sponsor_tiers": {"gold": 3, "silver": 8},
    })
    brief = extractor.brief_from_model(payload)
    assert brief.structured_data["content_type"] == "conference_talk"
    assert brief.structured_data["speakers"][0]["slides"] == ["a.pdf"]
    assert brief.structured_data["sponsor_tiers"] == {"gold": 3, "silver": 8}


# --- rejection of unknown top-level fields ---------------------------------

EXTRA_FIELD_BRIEFS = [
    ("one_extra_field", dict(SKILLS_BRIEF, price=10)),
    ("a_legacy_summary_field", dict(GENERAL_BRIEF, summary="old shape")),
    ("a_legacy_key_points_field", dict(GENERAL_BRIEF, key_points=["a"])),
    ("an_invented_content_field", dict(RECIPE_BRIEF, difficulty="hard")),
    ("two_extra_fields", dict(PRODUCT_BRIEF, rating=5, sku="abc")),
]


@pytest.mark.parametrize("name,payload", EXTRA_FIELD_BRIEFS,
                         ids=[n for n, _ in EXTRA_FIELD_BRIEFS])
def test_an_extra_top_level_field_is_rejected(name, payload):
    with pytest.raises(ValidationError):
        extractor.brief_from_model(payload)


def test_the_schema_itself_forbids_extra_fields():
    with pytest.raises(ValidationError):
        Brief(title="t", overview="o", surprise=1)


def test_a_brief_need_not_carry_every_field():
    """Partial output is normal; missing keys take their empty defaults."""
    brief = extractor.brief_from_model({"title": "t", "overview": "o"})
    assert brief.highlights == []
    assert brief.structured_data == {}
    assert brief.timestamps == []


def test_a_non_object_brief_is_rejected():
    with pytest.raises(ValidationError):
        extractor.brief_from_model("just a string")


def test_nulls_from_the_model_are_normalised_not_rejected():
    """Models answer with null for "nothing here"; that must not lose the brief."""
    brief = extractor.brief_from_model(
        dict(SKILLS_BRIEF, overview=None, topics=None,
             structured_data=None, actions=None))
    assert brief.overview == ""
    assert brief.topics == []
    assert brief.structured_data == {}
    assert len(brief.highlights) == 5, "the real content survives"


def test_a_brief_string_where_a_list_belongs_is_normalised():
    brief = extractor.brief_from_model(dict(GENERAL_BRIEF, topics="search"))
    assert brief.topics == ["search"]


def test_each_content_type_maps_onto_the_stored_category():
    """The existing category enum still works; the type comes from the brief."""
    cases = [
        (RECIPE_BRIEF, "recipe"),
        (TUTORIAL_BRIEF, "tutorial"),
        (PRODUCT_BRIEF, "product"),
        (SKILLS_BRIEF, "video"),
        (GENERAL_BRIEF, "article"),
    ]
    for payload, expected in cases:
        memory = extractor.memory_from_brief(extractor.brief_from_model(payload))
        assert memory.category == expected, payload["title"]


def test_all_terms_covers_what_a_user_would_search_for():
    brief = extractor.brief_from_model(SKILLS_BRIEF)
    terms = " ".join(brief.all_terms()).lower()
    for expected in ("claude skills", "skill 1", "claude", "install"):
        assert expected in terms, f"missing {expected}"


def test_the_prompt_states_the_brief_fields_and_no_content_types():
    """Phase 10 writes content-type prompts; this prompt must not pre-empt it."""
    prompt = extractor.BRIEF_SYSTEM_PROMPT.lower()
    for field in ("title", "overview", "highlights", "structured_data",
                  "entities", "topics", "intent", "actions", "timestamps"):
        assert field in prompt
    # The rule against padding and truncating is the part that matters most.
    assert "never cut it to three" in prompt


def test_highlights_are_capped_only_at_the_bound():
    """The cap bounds the model, it does not resize a real list.

    Five stays five. The cap only matters against an unbounded model output.
    """
    payload = dict(SKILLS_BRIEF, highlights=[
        f"point {i}" for i in range(extractor.MAX_HIGHLIGHTS + 10)])
    brief = extractor.brief_from_model(payload)
    assert len(brief.highlights) == extractor.MAX_HIGHLIGHTS
    memory = extractor.memory_from_brief(brief)
    assert memory.key_points == brief.highlights


def test_the_offline_heuristic_still_produces_a_valid_brief():
    """Ingest must survive with no provider configured."""
    brief = extractor._heuristic_brief("A mushroom risotto recipe. Serve hot.", "Risotto")
    assert isinstance(brief, Brief)
    assert brief.title
    memory = extractor.memory_from_brief(brief)
    assert memory.summary
    assert memory.category == "recipe"