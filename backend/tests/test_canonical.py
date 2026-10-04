from app.utils.canonical import canonical_url, source_type


def test_canonical_strips_tracking_and_fragment():
    assert canonical_url("http://www.Example.com/a/?utm_source=x&b=2&a=1#top") == "https://example.com/a?a=1&b=2"


def test_source_type():
    assert source_type("https://youtu.be/abc") == "youtube"
    assert source_type("https://amazon.com/item") == "product"
