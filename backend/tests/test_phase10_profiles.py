"""Phase 10: content-specific extraction profiles.

    python -m pytest tests/test_phase10_profiles.py -q

Exactly five profiles exist. Each one gets a sample, and every sample must
validate against the Phase 9 Brief schema as well as against its own profile
shape.
"""
import pytest
from pydantic import ValidationError

from app.schemas import Brief
from app.services import extractor, profiles

PROFILE_NAMES = ["list", "recipe", "product", "tutorial", "general"]

# --- one sample per profile ------------------------------------------------

LIST_SAMPLE = {
    "title": "5 Claude Skills That Save Me Hours",
    "overview": "Five Claude skills, and when each one is worth turning on.",
    "highlights": ["test-writer", "stack-trace-explainer", "changelog-drafter",
                   "flaky-test-finder", "migration-writer"],
    "structured_data": {"items": [
        "test-writer - 01:12", "stack-trace-explainer - 03:40",
        "changelog-drafter - 08:05", "flaky-test-finder - 12:30",
        "migration-writer - 17:55"]},
    "entities": ["Claude"], "topics": ["automation"],
    "intent": ["watch", "learn"], "actions": [], "timestamps": ["01:12"],
}

RECIPE_SAMPLE = {
    "title": "Mushroom Risotto",
    "overview": "A forty minute mushroom risotto for four.",
    "highlights": ["Toast the rice first", "Add stock slowly",
                   "Finish off the heat"],
    "structured_data": {
        "ingredients": ["300g arborio rice", "250g chestnut mushrooms",
                        "1l stock", "50g parmesan"],
        "steps": ["Dice the mushrooms", "Toast the rice in butter",
                  "Add stock a ladle at a time",
                  "Off the heat, beat in parmesan"],
        "time": "40 minutes", "temperature": "",
    },
    "entities": ["arborio rice"], "topics": ["italian"],
    "intent": ["cook"], "actions": [], "timestamps": [],
}

PRODUCT_SAMPLE = {
    "title": "Sony WH-1000XM5 Review",
    "overview": "Six months with Sony's flagship noise cancelling headphones.",
    "highlights": ["The noise cancelling is the best in the class",
                   "Thirty hour battery", "The case is bulky"],
    "structured_data": {
        "product_name": "Sony WH-1000XM5", "price": "$349",
        "specifications": ["30-hour battery", "250g", "Multipoint"],
        "pros": ["Excellent noise cancelling", "Comfortable for long sessions"],
        "cons": ["Bulky case", "No USB-C audio"],
        "use_case": "Frequent flyers and open-plan office use",
    },
    "entities": ["Sony"], "topics": ["audio"],
    "intent": ["buy"], "actions": [], "timestamps": [],
}

TUTORIAL_SAMPLE = {
    "title": "Set Up Postgres Logical Replication",
    "overview": "Replicate between two Postgres servers in five steps.",
    "highlights": ["Turn on logical WAL", "Create the publication",
                   "Start the subscriber"],
    "structured_data": {
        "goal": "A working logical replication slot between two servers",
        "prerequisites": ["Postgres 16 on both sides", "wal_level=logical"],
        "steps": ["Edit postgresql.conf", "Restart Postgres",
                  "CREATE PUBLICATION", "CREATE SUBSCRIPTION"],
        "tools": ["psql", "postgres"],
        "commands": ["ALTER SYSTEM SET wal_level = 'logical'",
                     "CREATE SUBSCRIPTION sub CONNECTION 'host=db2'"],
    },
    "entities": ["Postgres"], "topics": ["databases"],
    "intent": ["learn"], "actions": [], "timestamps": [],
}

GENERAL_SAMPLE = {
    "title": "Why We Rewrote Our Search",
    "overview": "Why keyword search was replaced with hybrid retrieval.",
    "highlights": ["Paraphrases defeated keyword search",
                   "Hybrid ranking improved recall"],
    "structured_data": {},
    "entities": ["Elasticsearch"], "topics": ["search"],
    "intent": ["read", "learn"], "actions": [], "timestamps": [],
}

# (profile name, sample, the text the classifier should read it as)
SAMPLES = [
    ("list", LIST_SAMPLE, "A video covering five Claude skills."),
    ("recipe", RECIPE_SAMPLE, "A mushroom risotto recipe with ingredients."),
    ("product", PRODUCT_SAMPLE, "A review of the Sony WH-1000XM5. Price $349."),
    ("tutorial", TUTORIAL_SAMPLE, "How to set up Postgres logical replication."),
    ("general", GENERAL_SAMPLE, "An engineering note on hybrid retrieval."),
]


@pytest.mark.parametrize("name,sample,content", SAMPLES,
                         ids=[name for name, _, _ in SAMPLES])
def test_each_profile_sample_is_picked_correctly(name, sample, content):
    assert profiles.classify(text=content, title=sample["title"]).name == name


@pytest.mark.parametrize("name,sample,content", SAMPLES,
                         ids=[name for name, _, _ in SAMPLES])
def test_each_profile_sample_validates_against_the_brief_schema(name, sample,
                                                                 content):
    """The Phase 9 contract holds for every profile."""
    profile = profiles.classify(text=content, title=sample["title"])
    brief = extractor.brief_from_model(sample, profile=profile)
    assert isinstance(brief, Brief)
    assert brief.structured_data["content_type"] == name
    assert brief.overview == sample["overview"]


@pytest.mark.parametrize("name,sample,content", SAMPLES,
                         ids=[name for name, _, _ in SAMPLES])
def test_each_profile_sample_carries_its_own_keys(name, sample, content):
    profile = profiles.classify(text=content, title=sample["title"])
    brief = extractor.brief_from_model(sample, profile=profile)
    for key in profile.keys:
        assert key in brief.structured_data, f"{name} lost {key}"


def test_the_list_profile_keeps_every_item():
    brief = extractor.brief_from_model(LIST_SAMPLE,
                                       profile=profiles.get_profile("list"))
    items = brief.structured_data["items"]
    assert items == LIST_SAMPLE["structured_data"]["items"]
    assert len(items) == 5, "a list of five stays five"
    # The items are the highlights too: the point of a list is the list.
    assert len(brief.highlights) == 5


def test_the_recipe_profile_carries_the_recipe_fields():
    brief = extractor.brief_from_model(RECIPE_SAMPLE,
                                       profile=profiles.get_profile("recipe"))
    data = brief.structured_data
    assert data["ingredients"] == RECIPE_SAMPLE["structured_data"]["ingredients"]
    assert data["steps"] == RECIPE_SAMPLE["structured_data"]["steps"]
    assert data["time"] == "40 minutes"
    assert data["temperature"] == "", "an unstated value stays empty, not invented"


def test_the_product_profile_carries_pros_and_cons():
    brief = extractor.brief_from_model(PRODUCT_SAMPLE,
                                       profile=profiles.get_profile("product"))
    data = brief.structured_data
    assert data["product_name"] == "Sony WH-1000XM5"
    assert data["price"] == "$349"
    assert len(data["pros"]) == 2
    assert len(data["cons"]) == 2
    assert data["use_case"]


def test_the_tutorial_profile_keeps_commands_verbatim():
    brief = extractor.brief_from_model(TUTORIAL_SAMPLE,
                                       profile=profiles.get_profile("tutorial"))
    data = brief.structured_data
    assert data["goal"]
    assert len(data["prerequisites"]) == 2
    assert data["commands"] == TUTORIAL_SAMPLE["structured_data"]["commands"]
    assert "wal_level = 'logical'" in data["commands"][0]


def test_the_general_profile_adds_no_keys_of_its_own():
    profile = profiles.get_profile("general")
    assert profile.keys == ()
    brief = extractor.brief_from_model(GENERAL_SAMPLE, profile=profile)
    assert brief.structured_data == {"content_type": "general"}


# --- the profile set is closed ---------------------------------------------

def test_exactly_five_profiles_exist():
    assert sorted(profiles.PROFILES) == sorted(PROFILE_NAMES)
    assert len(profiles.PROFILES) == 5


def test_an_unknown_profile_is_refused_rather_than_defaulted():
    with pytest.raises(KeyError):
        profiles.get_profile("podcast")
    with pytest.raises(KeyError):
        profiles.get_profile("")


def test_every_profile_has_prompt_instructions():
    for name in PROFILE_NAMES:
        profile = profiles.get_profile(name)
        assert profile.instructions.strip(), f"{name} has no instructions"


def test_each_profile_names_its_own_keys_in_the_prompt():
    """The prompt must ask for the keys the profile actually owns."""
    for name in PROFILE_NAMES:
        profile = profiles.get_profile(name)
        block = extractor.profile_for_prompt(profile)
        for key in profile.keys:
            assert key in block, f"{name} prompt never mentions {key}"
        assert name in block


def test_a_profile_key_the_model_omitted_is_still_present():
    """Every brief of a profile has the same shape, whatever the model sent."""
    brief = extractor.brief_from_model(
        {"title": "t", "overview": "o", "structured_data": {"steps": ["a"]}},
        profile=profiles.get_profile("recipe"))
    for key in profiles.RECIPE.keys:
        assert key in brief.structured_data
    assert brief.structured_data["steps"] == ["a"]
    assert brief.structured_data["ingredients"] == []


def test_a_profile_does_not_strip_free_form_keys():
    """Phase 9's promise still holds: unknown structured_data keys survive."""
    brief = extractor.brief_from_model(
        dict(LIST_SAMPLE, structured_data={
            "items": ["a", "b"], "speaker": "A", "sponsor": {"tier": "gold"}}),
        profile=profiles.get_profile("list"))
    assert brief.structured_data["speaker"] == "A"
    assert brief.structured_data["sponsor"] == {"tier": "gold"}


def test_a_profile_does_not_bypass_the_brief_schema():
    """The Phase 9 rejection rule applies whichever profile is in play."""
    with pytest.raises(ValidationError):
        extractor.brief_from_model(dict(LIST_SAMPLE, price=10),
                                   profile=profiles.get_profile("list"))


def test_the_profile_is_recorded_even_when_extraction_falls_back():
    """No provider configured: the brief still says which profile it used."""
    import asyncio

    brief = asyncio.run(extractor.extract_brief(
        "How to set up Postgres logical replication. Run the following steps.",
        "Replication guide", url="https://example.test/x"))
    assert isinstance(brief, Brief)
    assert brief.structured_data["content_type"] == "tutorial"
    for key in profiles.TUTORIAL.keys:
        assert key in brief.structured_data