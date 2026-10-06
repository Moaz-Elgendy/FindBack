from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.routers import ingest
from app.schemas import IngestResponse


def test_new_save_is_not_marked_as_existing():
    result = IngestResponse(id=uuid4(), status='pending', canonical_url='https://example.com')
    assert result.model_dump()['already_exists'] is False


@pytest.mark.parametrize('status', ['ready', 'pending', 'processing', 'failed'])
def test_repeat_save_is_marked_as_existing_including_failed_retry(monkeypatch, status):
    item = SimpleNamespace(id=uuid4(), user_id=uuid4(), content_id=uuid4(), status=status,
                           failure_reason='old failure')
    db = Mock()
    monkeypatch.setattr(ingest, '_record_save', Mock())
    monkeypatch.setattr(ingest, '_record_processing_job', Mock())
    monkeypatch.setattr(ingest, '_enqueue', Mock(return_value='pending'))
    result = ingest._touch(db, item, 'https://example.com')
    assert result.model_dump()['already_exists'] is True
    assert result.id == item.id
    db.commit.assert_called_once()
