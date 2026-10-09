"""Device registration: one row per device, owned by whoever signed in last.

The rule these tests exist to pin down is that `token` is unique ACROSS
accounts, not per user. A push registration is a property of the phone, and a
shared family device must follow whoever is actually signed in to it -- so
registering from a second account moves the row, and the first account must stop
being reachable through it.

Deleting is scoped to the caller's own token and answers 404 otherwise, because
a caller must not be able to probe for, or delete, a registration it does not
hold.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import DeviceToken
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users

ANDROID = 'fcm-token-abcdef0123456789'
IOS = 'apns-token-fedcba9876543210'


def _tokens(sessions, user_id):
    with sessions() as s:
        return [row[0] for row in s.query(DeviceToken.token)
                .filter(DeviceToken.user_id == user_id).all()]


def _owner(sessions, token):
    """Which user a token currently belongs to, as a string."""
    with sessions() as s:
        row = s.query(DeviceToken.user_id).filter(DeviceToken.token == token).first()
        return None if row is None else str(row[0])


def _all_tokens(sessions):
    with sessions() as s:
        return [row[0] for row in s.query(DeviceToken.token).all()]


# --- 1. register ------------------------------------------------------------

def test_registering_a_device_stores_it_for_the_caller(client, sessions, two_users):
    client.as_user(two_users['a'])
    response = client.request('POST', '/api/v1/devices',
                              json={'token': ANDROID, 'platform': 'android'})
    assert response.status_code == 201, response.text
    assert _tokens(sessions, two_users['a']) == [ANDROID]
    assert _tokens(sessions, two_users['b']) == []
    assert _owner(sessions, ANDROID) == str(two_users['a'])


def test_the_stored_row_records_platform_and_both_timestamps(client, sessions, two_users):
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': IOS, 'platform': 'ios'})
    with sessions() as s:
        row = s.query(DeviceToken).one()
        assert row.platform == 'ios'
        assert row.created_at is not None
        assert row.updated_at is not None


def test_registration_requires_authentication(client, two_users):
    client.anonymous()
    response = client.request('POST', '/api/v1/devices',
                              json={'token': ANDROID, 'platform': 'android'})
    assert response.status_code == 401


def test_an_unknown_platform_is_rejected(client, sessions, two_users):
    """The vocabulary is closed, so a typo cannot become an undeliverable row."""
    client.as_user(two_users['a'])
    for platform in ['web', 'Android', '', 'ios ']:
        response = client.request('POST', '/api/v1/devices',
                                  json={'token': ANDROID, 'platform': platform})
        assert response.status_code == 422, (platform, response.status_code)
    assert _all_tokens(sessions) == []


def test_a_blank_or_oversized_token_is_rejected(client, sessions, two_users):
    client.as_user(two_users['a'])
    for token in ['', '   ', 'x' * 4097]:
        response = client.request('POST', '/api/v1/devices',
                                  json={'token': token, 'platform': 'android'})
        assert response.status_code == 422, (len(token), response.status_code)
    assert _all_tokens(sessions) == []


def test_unexpected_fields_are_refused(client, sessions, two_users):
    """A caller cannot smuggle `user_id` and register for somebody else."""
    client.as_user(two_users['a'])
    response = client.request('POST', '/api/v1/devices',
                              json={'token': ANDROID, 'platform': 'android',
                                    'user_id': str(two_users['b'])})
    assert response.status_code == 422
    assert _owner(sessions, ANDROID) is None


def test_the_platform_is_enforced_by_the_database_too(client, sessions, two_users):
    """The route validates, but a check that can be forgotten is not a constraint."""
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    with pytest.raises(IntegrityError):
        with sessions() as s:
            s.execute(text("UPDATE device_tokens SET platform='web' WHERE token=:t"),
                      {'t': ANDROID})
            s.commit()


# --- 2. re-registering the same token ---------------------------------------

def test_registering_the_same_token_twice_keeps_one_row(client, sessions, two_users):
    """The app re-registers on every launch; that must not accumulate rows."""
    client.as_user(two_users['a'])
    for _ in range(3):
        assert client.request('POST', '/api/v1/devices',
                              json={'token': ANDROID, 'platform': 'android'}).status_code == 201
    assert _tokens(sessions, two_users['a']) == [ANDROID]
    with sessions() as s:
        assert s.query(DeviceToken).count() == 1


def test_re_registering_keeps_the_original_created_at(client, sessions, two_users):
    """The row should still say when the device was FIRST enrolled.

    `created_at` is pushed back a day first, so a value the upsert overwrote
    would be caught here rather than passing by coincidence within the same
    clock tick.
    """
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    with sessions() as s:
        s.execute(text("UPDATE device_tokens SET created_at = created_at - interval '1 day'"))
        s.commit()
        enrolled = s.query(DeviceToken).one().created_at

    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    with sessions() as s:
        row = s.query(DeviceToken).one()
        assert row.created_at == enrolled, "the upsert overwrote created_at"
        assert row.updated_at > row.created_at, "updated_at is what a re-registration moves"


def test_a_device_can_switch_platform_across_registrations(client, sessions, two_users):
    """An OS update can change the token's platform; the newest value wins."""
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'ios'})
    with sessions() as s:
        assert s.query(DeviceToken).count() == 1
        assert s.query(DeviceToken).one().platform == 'ios'


# --- 3. the token moves between accounts ------------------------------------

def test_registering_from_another_account_moves_the_token(client, sessions, two_users):
    """The shared-device case: one row, and it follows the current user."""
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    assert _owner(sessions, ANDROID) == str(two_users['a'])

    client.as_user(two_users['b'])
    assert client.request('POST', '/api/v1/devices',
                          json={'token': ANDROID, 'platform': 'android'}).status_code == 201

    assert _owner(sessions, ANDROID) == str(two_users['b']), "the row did not move"
    assert _tokens(sessions, two_users['a']) == [], "A kept a token it no longer holds"
    assert _tokens(sessions, two_users['b']) == [ANDROID]
    with sessions() as s:
        assert s.query(DeviceToken).count() == 1, "a move must not leave a second row"


def test_the_previous_owner_cannot_delete_the_token_after_it_moved(client, sessions, two_users):
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    client.as_user(two_users['b'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})

    client.as_user(two_users['a'])
    assert client.request('DELETE', f'/api/v1/devices/{ANDROID}').status_code == 404
    assert _owner(sessions, ANDROID) == str(two_users['b'])


def test_each_account_keeps_its_own_different_devices(client, sessions, two_users):
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    client.as_user(two_users['b'])
    client.request('POST', '/api/v1/devices', json={'token': IOS, 'platform': 'ios'})
    assert _tokens(sessions, two_users['a']) == [ANDROID]
    assert _tokens(sessions, two_users['b']) == [IOS]


def test_the_unique_index_holds_even_against_a_racing_writer(client, sessions, two_users):
    """Two inserts at once must not both win: the constraint is the guard."""
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    with pytest.raises(IntegrityError):
        with sessions() as s:
            s.add(DeviceToken(user_id=two_users['b'], token=ANDROID, platform='android'))
            s.commit()


# --- 4. deleting ------------------------------------------------------------

def test_deleting_own_device_removes_only_that_token(client, sessions, two_users):
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    client.request('POST', '/api/v1/devices', json={'token': IOS, 'platform': 'ios'})

    assert client.request('DELETE', f'/api/v1/devices/{ANDROID}').status_code == 204
    assert _tokens(sessions, two_users['a']) == [IOS]


def test_deleting_another_users_token_is_404_and_changes_nothing(client, sessions, two_users):
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})

    client.as_user(two_users['b'])
    denied = client.request('DELETE', f'/api/v1/devices/{ANDROID}')
    assert denied.status_code == 404, denied.text
    # B must not learn that the token exists at all.
    assert ANDROID not in denied.text
    assert _owner(sessions, ANDROID) == str(two_users['a'])


def test_deleting_an_unknown_token_is_404(client, sessions, two_users):
    client.as_user(two_users['a'])
    assert client.request('DELETE', f'/api/v1/devices/{uuid.uuid4()}').status_code == 404
    assert client.request('DELETE', '/api/v1/devices/never-registered').status_code == 404


def test_deleting_requires_authentication(client, sessions, two_users):
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    client.anonymous()
    assert client.request('DELETE', f'/api/v1/devices/{ANDROID}').status_code == 401
    assert _owner(sessions, ANDROID) == str(two_users['a'])


def test_deleting_twice_is_404_the_second_time(client, sessions, two_users):
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    assert client.request('DELETE', f'/api/v1/devices/{ANDROID}').status_code == 204
    assert client.request('DELETE', f'/api/v1/devices/{ANDROID}').status_code == 404


# --- 5. account deletion cascades ------------------------------------------

def test_deleting_an_account_removes_its_device_tokens(client, sessions, two_users, monkeypatch):
    """A token outliving the account would be delivered to a user who is gone."""
    from app.services import account
    monkeypatch.setattr(account, 'delete_provider_identity', lambda user: None)
    monkeypatch.setattr(account, 'delete_raw_snapshots', lambda ids: None)
    client.as_user(two_users['a'])
    client.request('POST', '/api/v1/devices', json={'token': ANDROID, 'platform': 'android'})
    client.as_user(two_users['b'])
    client.request('POST', '/api/v1/devices', json={'token': IOS, 'platform': 'ios'})

    # Back to A: deleting B's account here would leave A's token behind and the
    # assertion below would pass for entirely the wrong reason.
    client.as_user(two_users['a'])
    assert client.request('DELETE', '/api/v1/account').status_code == 204

    assert _all_tokens(sessions) == [IOS], "A's token outlived the account"
    assert _tokens(sessions, two_users['b']) == [IOS], "B's token was collateral damage"


def test_the_cascade_is_the_database_not_the_route(client, sessions, two_users, monkeypatch):
    """Deleting the user row alone must take the tokens with it."""
    from app.models import User
    with sessions() as s:
        s.add(DeviceToken(user_id=two_users['a'], token=ANDROID, platform='android'))
        s.commit()
        s.delete(s.get(User, two_users['a']))
        s.commit()
    with sessions() as s:
        assert s.query(DeviceToken).filter(DeviceToken.token == ANDROID).count() == 0


# --- 6. the table is not readable straight from the client ------------------

def test_device_tokens_are_protected_from_direct_client_access(sessions):
    """A push token is a live credential; Supabase roles must not read it."""
    with sessions() as s:
        enabled = s.execute(text(
            'SELECT relrowsecurity FROM pg_class WHERE oid = CAST(:name AS regclass)'),
            {'name': 'device_tokens'}).scalar()
        assert enabled is True
        grants = s.execute(text(
            "SELECT privilege_type FROM information_schema.table_privileges "
            "WHERE table_name = 'device_tokens' AND grantee = 'PUBLIC'")).all()
        assert grants == []