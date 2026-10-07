"""Copy an idle local public schema into an empty Supabase public schema.

Keeps both backups; restores atomically; never touches Supabase auth tables.
"""
import hashlib
import os
from pathlib import Path
import subprocess

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
settings = dotenv_values(root / '.env')
target = settings['TARGET_DATABASE_URL'].strip()
source = 'postgresql://findback:findback@127.0.0.1:55433/findback'
backup = Path.home() / '.local/state/findback/backups/phase9-deployment'
backup.mkdir(parents=True, exist_ok=True, mode=0o700)


def pg_env(url):
    parsed = make_url(url)
    return {**os.environ, 'PGHOST': parsed.host, 'PGPORT': str(parsed.port or 5432),
            'PGUSER': parsed.username, 'PGPASSWORD': parsed.password,
            'PGDATABASE': parsed.database, 'PGSSLMODE': parsed.query.get('sslmode', 'prefer')}


def inventory(engine):
    with engine.connect() as conn:
        conn.execute(text("SET TIME ZONE 'UTC'"))
        names = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars().all()
        result = {}
        for name in names:
            quoted = conn.dialect.identifier_preparer.quote(name)
            rows = conn.execute(text(f'SELECT to_jsonb(t)::text FROM public.{quoted} t ORDER BY to_jsonb(t)::text')).scalars().all()
            result[name] = (len(rows), hashlib.sha256('\n'.join(rows).encode()).hexdigest())
        return result


src = create_engine(source, connect_args={'connect_timeout': 10})
dst = create_engine(target, connect_args={'connect_timeout': 10})
assert not inventory(dst), 'Target public schema is not empty; refusing to overwrite'
with src.connect() as conn:
    assert conn.execute(text("SELECT count(*) FROM processing_jobs WHERE status='PROCESSING'")).scalar() == 0, 'Workers must be idle before migration'
expected = inventory(src)
for label, url in [('local', source), ('supabase-before', target)]:
    file = backup / f'{label}.dump'
    with file.open('wb') as handle:
        subprocess.run(['pg_dump', '--format=custom', '--schema=public', '--no-owner', '--no-acl'], env=pg_env(url), stdout=handle, check=True)
    file.chmod(0o600)
archive = backup / 'local.dump'
entries = subprocess.check_output(['pg_restore', '--list', str(archive)], text=True)
filtered = []
for line in entries.splitlines():
    if any(marker in line for marker in (' SCHEMA - public ', ' EXTENSION ', ' COMMENT - SCHEMA public ', ' COMMENT - EXTENSION ')):
        line = ';' + line
    filtered.append(line)
listing = backup / 'restore.list'
listing.write_text('\n'.join(filtered) + '\n')
sql = subprocess.check_output(['pg_restore', '--no-owner', '--no-acl', '--use-list', str(listing), '--file=-', str(archive)])
security = []
for name in expected:
    quoted = dst.dialect.identifier_preparer.quote(name)
    security.append(f'ALTER TABLE public.{quoted} ENABLE ROW LEVEL SECURITY;')
    security.append(f'REVOKE ALL ON TABLE public.{quoted} FROM anon, authenticated;')
prefix = b"""BEGIN;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
"""
script = prefix + sql + ('\n' + '\n'.join(security) + '\nCOMMIT;\n').encode()
log = backup / 'restore.log'
with log.open('wb') as handle:
    subprocess.run(['psql', '-X', '--set=ON_ERROR_STOP=1'], input=script, env=pg_env(target), stdout=handle, stderr=subprocess.STDOUT, check=True)
log.chmod(0o600)
actual = inventory(dst)
assert expected == actual, 'Data fingerprint mismatch; do not cut over'
with dst.connect() as conn:
    version = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
    unprotected = conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND NOT rowsecurity")).scalar()
    assert unprotected == 0
print('PASS: complete table counts and SHA256 row fingerprints match; all public tables protected by RLS')
print('Migration version:', version)
print('Table counts:', {name: count for name, (count, _) in actual.items()})
