"""Owned collections over the real API and database."""
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_phase16_multitenant import admin_engine, db, sessions, client, two_users


def test_collection_crud_is_private_and_delete_preserves_memories(client, two_users):
    a, b = two_users['a'], two_users['b']
    collection = str(uuid.uuid4())
    client.as_user(a)
    body = {'name': 'Claude', 'urls': ['https://example.test/alpha'] * 2}
    created = client.request('PUT', f'/api/v1/collections/{collection}', json=body)
    assert created.status_code == 200
    assert created.json()['urls'] == ['https://example.test/alpha']
    client.as_user(b)
    assert client.request('GET', '/api/v1/collections').json()['collections'] == []
    assert client.request('PUT', f'/api/v1/collections/{collection}', json=body).status_code == 404
    assert client.request('DELETE', f'/api/v1/collections/{collection}').status_code == 204
    client.as_user(a)
    assert client.request('GET', '/api/v1/collections').json()['collections'][0]['name'] == 'Claude'
    assert client.request('DELETE', f'/api/v1/collections/{collection}').status_code == 204
    assert client.request('GET', f"/api/v1/items/{two_users['a_item']}").status_code == 200


def test_collection_members_cannot_reference_another_users_item(client, two_users, sessions):
    client.as_user(two_users['a'])
    collection = str(uuid.uuid4())
    assert client.request('PUT', f'/api/v1/collections/{collection}', json={'name': 'Mine', 'urls': ['https://example.test/beta']}).status_code == 404
    assert client.request('PUT', f'/api/v1/collections/{collection}', json={'name': 'Mine', 'urls': []}).status_code == 200
    with sessions() as s:
        with pytest.raises(IntegrityError):
            s.execute(text('INSERT INTO collection_items (collection_id, item_id, user_id) VALUES (:c, :i, :u)'), {'c': collection, 'i': two_users['b_item'], 'u': two_users['a']})
            s.commit()
        s.rollback()
    assert client.request('PUT', f'/api/v1/collections/{collection}', json={'name': '   ', 'urls': []}).status_code == 422


def test_deleted_memory_disappears_from_collection(client, two_users):
    client.as_user(two_users['a'])
    collection = str(uuid.uuid4())
    assert client.request('PUT', f'/api/v1/collections/{collection}', json={'name': "x'); DROP TABLE items; --", 'urls': ['https://example.test/alpha']}).status_code == 200
    assert client.request('DELETE', f"/api/v1/items/{two_users['a_item']}").status_code == 204
    result = client.request('GET', '/api/v1/collections').json()['collections']
    assert result[0]['urls'] == []


def test_collection_tables_have_rls_and_no_public_select(sessions):
    with sessions() as s:
        for table in ('collections', 'collection_items'):
            enabled = s.execute(text('SELECT relrowsecurity FROM pg_class WHERE oid = CAST(:name AS regclass)'), {'name': table}).scalar()
            assert enabled is True
            grants = s.execute(text("SELECT privilege_type FROM information_schema.table_privileges WHERE table_name = :name AND grantee = 'PUBLIC'"), {'name': table}).all()
            assert grants == []


def test_collection_migration_rolls_back_without_deleting_memories(db, sessions, two_users):
    from alembic import command
    from app import database
    cfg = database.alembic_config()
    cfg.set_main_option('sqlalchemy.url', db.url.render_as_string(hide_password=False))
    command.downgrade(cfg, '0013_job_attempt_token')
    with sessions() as s:
        assert s.execute(text('SELECT count(*) FROM items WHERE id = :id'), {'id': two_users['a_item']}).scalar() == 1
        assert s.execute(text("SELECT to_regclass('collections')")).scalar() is None
    command.upgrade(cfg, 'head')
    with sessions() as s:
        assert s.execute(text("SELECT to_regclass('collections')")).scalar() is not None
