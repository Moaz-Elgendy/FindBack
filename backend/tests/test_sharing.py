"""Immutable, owner-scoped sharing with independent recipient copies."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from test_phase16_multitenant import admin_engine, db, sessions, client, two_users


def make_share(client, two_users):
    client.as_user(two_users['a'])
    response = client.request('POST', f"/api/v1/items/{two_users['a_item']}/share")
    assert response.status_code == 201, response.text
    return response.json()


def test_snapshot_is_sanitized_immutable_and_redemption_idempotent(client, sessions, two_users):
    client.as_user(two_users['a'])
    assert client.request('PUT', '/api/v1/account/profile', json={'display_name': 'Alex'}).status_code == 200
    with sessions() as session:
        session.execute(text("UPDATE items SET edited_title='Shared title', edited_summary='Shared brief', tags=ARRAY['secret'], raw_text='private extraction' WHERE id=:id"), {'id': two_users['a_item']})
        session.commit()
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    with sessions() as session:
        session.execute(text("UPDATE items SET edited_title='Later title' WHERE id=:id"), {'id': two_users['a_item']})
        session.commit()
    client.as_user(two_users['b'])
    first = client.request('POST', f'/api/v1/shares/{token}/redeem')
    assert first.status_code == 200, first.text
    data = first.json()
    assert data['title'] == 'Shared title'
    assert data['summary'] == 'Shared brief'
    assert data['shared_by'] == 'Alex'
    assert data['tags'] == []
    assert data['source_domain'] == 'example.test'
    assert 'private extraction' not in first.text
    assert 'a@example.test' not in first.text
    assert client.request('POST', f'/api/v1/shares/{token}/redeem').json()['id'] == data['id']
    with sessions() as session:
        assert session.execute(text('SELECT raw_text FROM items WHERE id=:id'), {'id': data['id']}).scalar() is None
    client.as_user(two_users['a'])
    assert client.request('DELETE', f"/api/v1/account/shares/{share['id']}").status_code == 204
    client.as_user(two_users['b'])
    assert client.request('POST', f'/api/v1/shares/{token}/redeem').status_code == 410
    assert client.request('GET', f"/api/v1/items/{data['id']}").json()['title'] == 'Shared title'


def test_expiry_owner_scope_and_neutral_attribution(client, sessions, two_users):
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    client.as_user(two_users['b'])
    assert client.request('GET', '/api/v1/account/shares').json() == {'shares': []}
    assert client.request('DELETE', f"/api/v1/account/shares/{share['id']}").status_code == 404
    assert client.request('POST', f'/api/v1/shares/{token}/redeem').json()['shared_by'] is None
    with sessions() as session:
        session.execute(text('UPDATE memory_shares SET expires_at=:expiry WHERE id=:id'), {'expiry': datetime.now(timezone.utc) - timedelta(seconds=1), 'id': share['id']})
        session.commit()
    assert client.request('POST', f'/api/v1/shares/{token}/redeem').status_code == 410
    assert client.request('POST', '/api/v1/shares/invalid/redeem').status_code == 404


def test_existing_save_is_never_overwritten_and_foreign_source_is_hidden(client, two_users):
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    own = client.request('GET', f"/api/v1/items/{two_users['a_item']}").json()
    assert client.request('POST', f'/api/v1/shares/{token}/redeem').json()['id'] == own['id']
    client.as_user(two_users['b'])
    assert client.request('POST', f"/api/v1/items/{two_users['a_item']}/share").status_code == 404


def test_profile_rejects_email_and_unknown_fields(client, two_users):
    client.as_user(two_users['a'])
    for value in ({'display_name': 'a@example.test'}, {'display_name': 'a' * 81}, {'email': 'x@y.test'}):
        assert client.request('PUT', '/api/v1/account/profile', json=value).status_code == 422


def test_source_deletion_preserves_snapshot_sender_deletion_invalidates_only_link(client, sessions, two_users, monkeypatch):
    from app.services import account
    monkeypatch.setattr(account, 'delete_provider_identity', lambda _: None)
    monkeypatch.setattr(account, 'delete_raw_snapshots', lambda _: None)
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    assert client.request('DELETE', f"/api/v1/items/{two_users['a_item']}").status_code == 204
    client.as_user(two_users['b'])
    saved = client.request('POST', f'/api/v1/shares/{token}/redeem').json()
    assert saved['id']
    client.as_user(two_users['a'])
    assert client.request('DELETE', '/api/v1/account').status_code == 204
    client.as_user(two_users['b'])
    assert client.request('POST', f'/api/v1/shares/{token}/redeem').status_code == 404
    assert client.request('GET', f"/api/v1/items/{saved['id']}").status_code == 200


def test_expiry_setting_rate_limit_and_token_storage(client, sessions, two_users, monkeypatch):
    from app.models import MemoryShare
    monkeypatch.setenv('SHARE_LINK_TTL_DAYS', '7')
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    with sessions() as session:
        row = session.get(MemoryShare, share['id'])
        assert row.expires_at - row.created_at == timedelta(days=7)
        assert token not in str(row.snapshot)
        assert token != row.token_hash
        session.add_all(MemoryShare(user_id=two_users['a'], token_hash=str(i).zfill(64),
            snapshot=row.snapshot, created_at=row.created_at, expires_at=row.expires_at) for i in range(19))
        session.commit()
    response = client.request('POST', f"/api/v1/items/{two_users['a_item']}/share")
    assert response.status_code == 429
    assert 'Retry-After' in response.headers


def test_landing_exposes_no_memory_or_email_and_has_no_invented_store_link(client, two_users, monkeypatch):
    monkeypatch.delenv('ANDROID_PLAY_STORE_URL', raising=False)
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    page = client.request('GET', '/s/' + token)
    assert page.status_code == 200
    assert 'example.test' not in page.text
    assert 'play.google.com' not in page.text
    assert page.headers['referrer-policy'] == 'no-referrer'
    assert page.headers['cache-control'] == 'no-store'
    assert client.request('GET', '/.well-known/assetlinks.json').json() == []
    fingerprint = ':'.join(['AB'] * 32)
    monkeypatch.setenv('ANDROID_APP_LINK_FINGERPRINTS', fingerprint)
    value = client.request('GET', '/.well-known/assetlinks.json').json()[0]
    assert value['target']['sha256_cert_fingerprints'] == [fingerprint]


def test_credential_bearing_sources_cannot_create_public_share(client, sessions, two_users):
    with sessions() as session:
        session.execute(text("UPDATE items SET canonical_url='https://example.test/private?access_token=secret' WHERE id=:id"), {'id': two_users['a_item']})
        session.commit()
    client.as_user(two_users['a'])
    response = client.request('POST', f"/api/v1/items/{two_users['a_item']}/share")
    assert response.status_code == 409
    assert 'secret' not in response.text


def test_existing_recipient_source_keeps_edits(client, sessions, two_users):
    with sessions() as session:
        session.execute(text('UPDATE items SET canonical_url=(SELECT canonical_url FROM items WHERE id=:source), edited_summary=:brief WHERE id=:recipient'), {'source': two_users['a_item'], 'recipient': two_users['b_item'], 'brief': 'Keep my own edit'})
        session.commit()
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    client.as_user(two_users['b'])
    result = client.request('POST', f'/api/v1/shares/{token}/redeem').json()
    assert result['id'] == two_users['b_item']
    assert result['summary'] == 'Keep my own edit'


def test_concurrent_redemptions_create_one_copy_without_processing_jobs(client, sessions, two_users):
    from concurrent.futures import ThreadPoolExecutor
    from app.models import User
    from app.routers.sharing import redeem
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    def save(_):
        with sessions() as session:
            user = session.get(User, two_users['b'])
            return str(redeem(token, db=session, user=user).id)
    with ThreadPoolExecutor(max_workers=6) as pool:
        saved = list(pool.map(save, range(6)))
    assert len(set(saved)) == 1
    with sessions() as session:
        assert session.execute(text('SELECT count(*) FROM share_redemptions WHERE user_id=:u'), {'u': two_users['b']}).scalar() == 1
        assert session.execute(text('SELECT content_id FROM items WHERE id=:id'), {'id': saved[0]}).scalar() is None


def test_guest_must_sign_in_to_create_or_redeem_share(client, sessions, two_users):
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    with sessions() as session:
        session.execute(text("UPDATE users SET auth_provider='findback:guest' WHERE id=:u"), {'u': two_users['b']})
        session.commit()
    client.as_user(two_users['b'])
    assert client.request('POST', f'/api/v1/shares/{token}/redeem').status_code == 401
    assert client.request('POST', f"/api/v1/items/{two_users['b_item']}/share").status_code == 401


def test_redemption_losing_to_another_save_opens_winner_without_overwrite(client, sessions, two_users, monkeypatch):
    from app.models import Item, User
    from app.routers.sharing import redeem
    share = make_share(client, two_users)
    token = share['url'].rsplit('/', 1)[1]
    with sessions() as session:
        source = session.get(Item, two_users['a_item'])
        url = source.canonical_url
        user = session.get(User, two_users['b'])
        flush = session.flush
        winner_id = []
        def competing_flush(*args, **kwargs):
            if not winner_id and any(isinstance(row, Item) for row in session.new):
                with sessions() as winner:
                    row = Item(user_id=user.id, url=url, canonical_url=url, status='ready',
                               edited_summary='Keep concurrent edit')
                    winner.add(row)
                    winner.commit()
                    winner_id.append(str(row.id))
            return flush(*args, **kwargs)
        monkeypatch.setattr(session, 'flush', competing_flush)
        result = redeem(token, db=session, user=user)
        assert str(result.id) == winner_id[0]
        assert result.edited_summary == 'Keep concurrent edit'


def test_failed_brief_cannot_be_presented_as_a_ready_shared_copy(client, sessions, two_users):
    with sessions() as session:
        session.execute(text('UPDATE items SET needs_retry=true WHERE id=:id'), {'id': two_users['a_item']})
        session.commit()
    client.as_user(two_users['a'])
    assert client.request('POST', f"/api/v1/items/{two_users['a_item']}/share").status_code == 409
