"""Upgrade both released and corrected snapshot schemas without losing history."""
import pytest
from sqlalchemy import create_engine, inspect, text

from tests.test_phase16_migration import admin_engine, _fresh_db, _drop, _upgrade


@pytest.mark.parametrize('legacy', [False, True])
def test_snapshot_reconciliation_preserves_history_and_owner_cascade(admin_engine, legacy):
    admin, name, url = _fresh_db(admin_engine, 'fb_snapshot_reconcile')
    engine = None
    try:
        _upgrade(url, '0024_weekly_snapshot_delivered')
        engine = create_engine(url)
        with engine.begin() as conn:
            if legacy:
                conn.execute(text('ALTER TABLE snapshot_items ADD CONSTRAINT legacy_save_fk '
                                  'FOREIGN KEY (save_id) REFERENCES items(id) ON DELETE CASCADE'))
                for table in ('weekly_snapshots', 'snapshot_items'):
                    conn.execute(text(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY'))
                    conn.execute(text(f'GRANT ALL ON {table} TO PUBLIC'))
            owner = conn.execute(text("INSERT INTO users(email) VALUES ('reconcile@example.test') RETURNING id")).scalar_one()
            save = conn.execute(text("INSERT INTO items(user_id,url,canonical_url,status) "
                                     "VALUES (:owner,'https://example.test/reconcile','https://example.test/reconcile','ready') RETURNING id"),
                                {'owner': owner}).scalar_one()
            snapshot = conn.execute(text('INSERT INTO weekly_snapshots(user_id) VALUES (:owner) RETURNING id'),
                                    {'owner': owner}).scalar_one()
            conn.execute(text('INSERT INTO snapshot_items(snapshot_id,save_id) VALUES (:snapshot,:save)'),
                         {'snapshot': snapshot, 'save': save})
        _upgrade(url)
        with engine.begin() as conn:
            flags = dict(conn.execute(text("SELECT relname,relrowsecurity FROM pg_class "
                                           "WHERE relname IN ('weekly_snapshots','snapshot_items')")).all())
            assert flags == {'weekly_snapshots': True, 'snapshot_items': True}
            assert conn.execute(text("SELECT count(*) FROM pg_class c, "
                                     "LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a "
                                     "WHERE c.relname IN ('weekly_snapshots','snapshot_items') AND a.grantee=0")).scalar_one() == 0
            assert all('save_id' not in fk['constrained_columns']
                       for fk in inspect(conn).get_foreign_keys('snapshot_items'))
            conn.execute(text('DELETE FROM items WHERE id=:save'), {'save': save})
            assert conn.execute(text('SELECT count(*) FROM snapshot_items WHERE snapshot_id=:snapshot'),
                                {'snapshot': snapshot}).scalar_one() == 1
            conn.execute(text('DELETE FROM users WHERE id=:owner'), {'owner': owner})
            assert conn.execute(text('SELECT count(*) FROM snapshot_items')).scalar_one() == 0
            assert conn.execute(text('SELECT count(*) FROM weekly_snapshots')).scalar_one() == 0
    finally:
        _drop(admin, name, engine)
