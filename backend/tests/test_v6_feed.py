from test_phase16_multitenant import admin_engine, db, sessions, client, two_users


def test_feed_etag_is_private_and_changes_with_edits(client, two_users):
    client.as_user(two_users['a'])
    first = client.request('GET', '/api/v1/items')
    assert first.status_code == 200
    etag = first.headers['etag']
    assert 'private' in first.headers['cache-control']
    assert 'Authorization' in first.headers['vary'].split(', ')
    unchanged = client.request('GET', '/api/v1/items', headers={'If-None-Match': etag})
    assert unchanged.status_code == 304
    assert unchanged.content == b''
    client.request('PATCH', '/api/v1/items/' + two_users['a_item'], json={'summary': 'A new private summary'})
    changed = client.request('GET', '/api/v1/items', headers={'If-None-Match': etag})
    assert changed.status_code == 200
    assert changed.headers['etag'] != etag
    client.as_user(two_users['b'])
    other = client.request('GET', '/api/v1/items', headers={'If-None-Match': changed.headers['etag']})
    assert other.status_code == 200
    assert 'A new private summary' not in other.text


def test_feed_etag_respects_weak_and_multiple_tags(client, two_users):
    client.as_user(two_users['a'])
    etag = client.request('GET', '/api/v1/items').headers['etag']
    assert client.request('GET', '/api/v1/items', headers={'If-None-Match': '"other", W/' + etag}).status_code == 304
