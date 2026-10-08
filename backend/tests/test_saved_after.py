"""Date cutoffs are enforced before pagination and search candidate limits."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from test_phase16_multitenant import admin_engine, db, sessions, client, two_users, _ready_item

CUTOFF = datetime(2026, 9, 24, tzinfo=timezone.utc)


@pytest.mark.parametrize('path', ['/api/v1/items', '/api/v1/search'])
@pytest.mark.parametrize('stamp', ['2026-09-24T00:00:00Z', '2026-09-24T02:00:00+02:00'])
def test_inclusive_cutoff_and_ownership(client, sessions, two_users, path, stamp):
    with sessions() as session:
        session.execute(text('UPDATE items SET created_at=:at WHERE id=:id'),
                        {'at': CUTOFF, 'id': two_users['a_item']})
        old, _ = _ready_item(sessions, two_users['a'], 'https://old.test/date')
        session.execute(text('UPDATE items SET created_at=:at WHERE id=:id'), {'at': CUTOFF - timedelta(microseconds=1), 'id': old})
        session.commit()
    client.as_user(two_users['a'])
    response = client.request('GET', path, params={'q': 'sharedtoken', 'saved_after': stamp, 'limit': 1})
    assert response.status_code == 200
    rows = response.json()['items' if path.endswith('items') else 'results']
    assert [row['id'] for row in rows] == [two_users['a_item']]
    if path.endswith('items'):
        assert response.json()['next_cursor'] is None


@pytest.mark.parametrize('path', ['/api/v1/items', '/api/v1/search'])
@pytest.mark.parametrize('stamp', ['garbage', '2026-09-24T00:00:00'])
def test_invalid_cutoff(client, two_users, path, stamp):
    client.as_user(two_users['a'])
    assert client.request('GET', path, params={'q': 'sharedtoken', 'saved_after': stamp}).status_code == 422


def test_search_cutoff_precedes_candidate_limit(client, sessions, two_users):
    with sessions() as session:
        session.execute(text('UPDATE items SET created_at=:at, search_text=:words WHERE id=:id'),
                        {'at': CUTOFF, 'words': 'sharedtoken', 'id': two_users['a_item']})
        for index in range(55):
            old, _ = _ready_item(sessions, two_users['a'], f'https://old.test/{index}')
            session.execute(text('UPDATE items SET created_at=:at, search_text=:words WHERE id=:id'), {'at': CUTOFF - timedelta(days=1), 'words': 'sharedtoken ' * 30, 'id': old})
        session.commit()
    client.as_user(two_users['a'])
    response = client.request('GET', '/api/v1/search', params={'q': 'sharedtoken', 'saved_after': CUTOFF.isoformat()})
    assert response.status_code == 200
    assert [row['id'] for row in response.json()['results']] == [two_users['a_item']]


def test_library_cutoff_with_cursor(client, sessions, two_users):
    recent, _ = _ready_item(sessions, two_users['a'], 'https://recent.test/date')
    with sessions() as session:
        for item_id, stamp in [(recent, CUTOFF + timedelta(seconds=1)),
                               (two_users['a_item'], CUTOFF)]:
            session.execute(text('UPDATE items SET created_at=:at WHERE id=:id'), {'at': stamp, 'id': item_id})
        session.commit()
    client.as_user(two_users['a'])
    params = {'saved_after': CUTOFF.isoformat(), 'limit': 1}
    first = client.request('GET', '/api/v1/items', params=params).json()
    assert [row['id'] for row in first['items']] == [recent]
    second = client.request('GET', '/api/v1/items', params={**params, 'cursor': first['next_cursor']}).json()
    assert [row['id'] for row in second['items']] == [two_users['a_item']]
    assert second['next_cursor'] is None


@pytest.mark.parametrize('path', ['/api/v1/items', '/api/v1/search'])
def test_cutoff_combines_with_intelligence(client, sessions, two_users, path):
    with sessions() as session:
        session.execute(text("UPDATE items SET created_at=:at, intent='project' WHERE id=:id"),
                        {'at': CUTOFF, 'id': two_users['a_item']})
        session.commit()
    client.as_user(two_users['a'])
    for intent, expected in [('project', [two_users['a_item']]), ('learning', [])]:
        response = client.request('GET', path, params={'q': 'sharedtoken', 'saved_after': CUTOFF.isoformat(),
                                                       'intelligence': '{"intent":"' + intent + '"}'})
        assert response.status_code == 200
        assert [row['id'] for row in response.json()['items' if path.endswith('items') else 'results']] == expected
