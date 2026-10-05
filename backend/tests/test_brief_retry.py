from types import SimpleNamespace
from app.services import brief_retry


def test_retry_cap_and_exponential_backoff(monkeypatch):
    monkeypatch.setenv('MEDIA_RETRY_MAX_ATTEMPTS', '3')
    item = SimpleNamespace(url='https://facebook.com/reel/x', evidence_bundle={'evidence_level': 'partial'},
                           processing_metadata={'media_attempts': 1})
    assert brief_retry.should_retry(item)
    assert brief_retry.delay_for(2) == 2 * brief_retry.delay_for(1)
    item.processing_metadata['media_attempts'] = 3
    assert not brief_retry.should_retry(item)
    item.processing_metadata['media_attempts'] = 1
    item.evidence_bundle['evidence_level'] = 'full_transcript'
    assert not brief_retry.should_retry(item)
