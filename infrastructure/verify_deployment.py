"""Validate resolved production configuration without printing credentials."""
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config = json.loads(subprocess.check_output([
    'docker', 'compose', '--env-file', str(root / '.env'),
    '-f', str(root / 'infrastructure/compose.production.yml'), 'config', '--format', 'json'
]))
services = config['services']
assert set(services) == {'api', 'worker', 'outbox', 'beat', 'redis', 'caddy'}
for name in ('api', 'worker', 'outbox', 'beat'):
    env = services[name]['environment']
    assert env['DATABASE_URL'] == env['TARGET_DATABASE_URL']
    assert 'sslmode=require' in env['DATABASE_URL']
    assert str(env['DEV_AUTH_ENABLED']).lower() == 'false'
    assert env['SCHEMA_BOOTSTRAP'] == 'none'
    assert not services[name].get('ports')
assert not services['redis'].get('ports')
assert {int(p['published']) for p in services['caddy']['ports']} == {80, 443}
assert '--reload' not in services['api']['command']
assert '--concurrency=1' in services['worker']['command']
assert 'beat' in services['beat']['command']
assert not any(v.get('type') == 'bind' for v in services['api'].get('volumes', []))
print('PASS: private API/Redis, HTTPS ports, Supabase TLS, auth enabled, manual schema bootstrap, worker and beat, no API source mounts')
