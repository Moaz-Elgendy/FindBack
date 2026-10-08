"""Real owner-scoped reminder API; overdue records stay until acknowledged."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users


def target(hours=2):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def test_reminder_set_replace_remove_and_ack(client, sessions, two_users):
    client.as_user(two_users['a'])
    path = f"/api/v1/items/{two_users['a_item']}/reminder"
    first = target()
    r = client.request('PUT', path, json={'scheduled_at': first, 'time_zone': 'America/New_York'})
    assert r.status_code == 200
    assert r.json()['time_zone'] == 'America/New_York'
    assert client.request('GET', path).json()['scheduled_at'] == r.json()['scheduled_at']
    second = target(3)
    client.request('PUT', path, json={'scheduled_at': second, 'time_zone': 'Africa/Cairo'})
    client.request('POST', path + '/delivered', json={'scheduled_at': first})
    assert len(client.request('GET', '/api/v1/reminders').json()['reminders']) == 1
    due = target(-1)
    with sessions() as session:
        session.execute(text('UPDATE reminders SET scheduled_at=:at'), {'at': due})
        session.commit()
    client.request('POST', path + '/delivered', json={'scheduled_at': due})
    assert client.request('GET', '/api/v1/reminders').json()['reminders'] == []
    assert client.request('DELETE', path).status_code == 204
    assert client.request('GET', path).json() is None


def test_reminders_validate_zone_time_owner_and_deleted(client, two_users):
    path = f"/api/v1/items/{two_users['a_item']}/reminder"
    client.as_user(two_users['a'])
    for payload in [{'scheduled_at': target(-1), 'time_zone': 'UTC'},
                    {'scheduled_at': '2027-01-01T12:00:00', 'time_zone': 'UTC'},
                    {'scheduled_at': target(), 'time_zone': 'invented/zone'}]:
        assert client.request('PUT', path, json=payload).status_code == 422
    client.as_user(two_users['b'])
    for method in ['GET', 'DELETE', 'PUT']:
        assert client.request(method, path, json={'scheduled_at': target(), 'time_zone': 'UTC'} if method == 'PUT' else None).status_code == 404
    client.as_user(two_users['a'])
    client.request('PUT', path, json={'scheduled_at': target(), 'time_zone': 'UTC'})
    client.request('DELETE', path.removesuffix('/reminder') + '?undoable=true')
    assert client.request('GET', '/api/v1/reminders').json()['reminders'] == []
    assert client.request('GET', path).status_code == 404
    client.request('POST', path.removesuffix('/reminder') + '/restore')
    assert len(client.request('GET', '/api/v1/reminders').json()['reminders']) == 1


def test_overdue_more_than_day_is_not_dropped_or_title_leaked(client, sessions, two_users):
    client.as_user(two_users['a'])
    path = f"/api/v1/items/{two_users['a_item']}/reminder"
    assert client.request('PUT', path, json={'scheduled_at': target(), 'time_zone': 'UTC'}).status_code == 200
    with sessions() as session:
        session.execute(text("UPDATE reminders SET scheduled_at=now()-interval '3 days'"))
        session.commit()
    rows = client.request('GET', '/api/v1/reminders').json()['reminders']
    assert len(rows) == 1
    assert set(rows[0]) == {'item_id', 'scheduled_at', 'time_zone', 'delivered_at'}
