import pytest
from app.utils.canonical import canonical_url


@pytest.mark.parametrize('url', [
    'https://youtu.be/dQw4w9WgXcQ?si=tracking',
    'https://m.youtube.com/shorts/dQw4w9WgXcQ',
    'https://youtube.com/embed/dQw4w9WgXcQ',
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share',
])
def test_youtube_public_video_urls_converge(url):
    assert canonical_url(url) == 'https://youtube.com/watch?v=dQw4w9WgXcQ'


def test_meaningful_article_queries_and_paths_do_not_collide():
    assert canonical_url('https://example.com/a?page=1') != canonical_url('https://example.com/a?page=2')
    assert canonical_url('https://example.com/a') != canonical_url('https://example.com/a/b')
    assert canonical_url('https://example.com/a?token=one') != canonical_url('https://example.com/a?token=two')
    assert canonical_url('https://example.com:8443/a') != canonical_url('https://example.com/a')


def test_no_false_platform_host_match():
    assert canonical_url('https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ') == 'https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ'
