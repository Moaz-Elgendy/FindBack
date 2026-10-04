"""Content identity: platform id, then canonical URL, then content hash."""
from app.utils.dedupe import (
    content_hash, dedupe_key, dedupe_key_for_row, normalize_content, platform_id,
)


# --- tier 1: platform ids -------------------------------------------------

def test_youtube_id_is_read_from_every_url_shape():
    for url in (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=42",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "youtube.com/watch?v=dQw4w9WgXcQ",
    ):
        assert platform_id(url) == "youtube:dQw4w9WgXcQ", url


def test_different_youtube_videos_get_different_ids():
    assert platform_id("https://youtu.be/aaaaaaaaaaa") != \
        platform_id("https://youtu.be/bbbbbbbbbbb")


def test_unsupported_platforms_have_no_platform_id():
    assert platform_id("https://example.com/a") is None
    assert platform_id("https://tiktok.com/@x/video/123") is None
    assert platform_id("") is None


def test_youtube_lookalike_path_is_not_an_id():
    # /feed/ is not a video page, so no platform id may be claimed.
    assert platform_id("https://www.youtube.com/feed/subscriptions") is None


def test_non_youtube_host_that_contains_youtube_is_not_matched():
    assert platform_id("https://notyoutube.com/watch?v=dQw4w9WgXcQ") is None


# --- tier 2: canonical URL ------------------------------------------------

def test_platform_id_wins_over_the_url():
    # Different hosts, so the URL tier would give a different key; the
    # platform id must take precedence.
    a = dedupe_key(url="https://youtu.be/dQw4w9WgXcQ")
    b = dedupe_key(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert a == b == "youtube:dQw4w9WgXcQ"


def test_tracking_parameters_do_not_change_the_key():
    assert dedupe_key(url="https://example.com/a?utm_source=x") == \
        dedupe_key(url="https://example.com/a")


def test_url_key_is_namespaced():
    assert dedupe_key(url="https://example.com/a").startswith("url:")


def test_namespacing_prevents_cross_tier_collision():
    # A page whose URL looks like a YouTube id must not collide with the video.
    assert dedupe_key(url="https://example.com/dQw4w9WgXcQ") != \
        platform_id("https://youtu.be/dQw4w9WgXcQ")


def test_different_content_different_url_keys():
    assert dedupe_key(url="https://example.com/a") != \
        dedupe_key(url="https://example.com/b")


# --- tier 3: normalized content ------------------------------------------

def test_normalization_is_case_punctuation_and_space_insensitive():
    base = normalize_content("Chicken, Cream, Mushroom!")
    assert base == normalize_content("chicken   cream  mushroom")
    assert base == normalize_content("CHICKEN CREAM MUSHROOM")


def test_normalization_folds_accents():
    assert normalize_content("Crème Brûlée") == normalize_content("creme brulee")


def test_content_hash_is_stable_and_order_sensitive():
    a = content_hash("one two three")
    b = content_hash("One, Two, THREE!")
    assert a == b
    assert content_hash("three two one") != a


def test_content_hash_is_none_for_empty_content():
    assert content_hash("") is None
    assert content_hash("   ") is None
    assert content_hash(None) is None


def test_content_hash_is_only_used_when_there_is_no_url():
    assert dedupe_key(url="", content="hello world").startswith("sha256:")
    # A URL always wins, even when content is present.
    assert dedupe_key(url="https://example.com/a", content="hello").startswith("url:")


def test_no_identifiers_at_all_yields_no_key():
    assert dedupe_key(url="", content="") is None


def test_row_key_uses_url_when_available_not_content():
    """The key must not change when extraction adds text, or the UNIQUE index
    would be invalidated mid-pipeline."""
    before = dedupe_key_for_row(url="https://example.com/a", title="",
                                summary="", key_points=[])
    after = dedupe_key_for_row(url="https://example.com/a", title="A Title",
                               summary="A summary", key_points=["one"])
    assert before == after == "url:https://example.com/a"


def test_row_key_falls_back_to_content_without_a_url():
    key = dedupe_key_for_row(url="", title="A Title", summary="A summary",
                             key_points=["one"])
    assert key == content_hash("A Title A summary one")