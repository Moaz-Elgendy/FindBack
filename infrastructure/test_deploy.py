"""Execute the real deployment script with isolated Docker boundary doubles."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

script = Path(__file__).resolve().with_name('deploy.sh')
image = '123456789012.dkr.ecr.eu-west-1.amazonaws.com/findback-backend:new'
for healthy in (True, False):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'infrastructure').mkdir()
        release = root / 'releases/new'
        (release / 'infrastructure').mkdir(parents=True)
        for name in ('Caddyfile', 'compose.production.yml'):
            (root / 'infrastructure' / name).write_text('old ' + name)
            (release / 'infrastructure' / name).write_text('new ' + name)
        envfile = root / '.env'
        envfile.write_text('PROVIDER_SECRET=keep-this-value\n')
        envfile.chmod(0o640)
        original_owner = (envfile.stat().st_uid, envfile.stat().st_gid)
        binary = root / 'bin'
        binary.mkdir()
        docker = binary / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['DOCKER_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')
if args[0]=='run': print('mock-token')
elif args[0]=='login': sys.stdin.read()
elif args[0]=='inspect': print('findback-backend:production')
elif 'ps' in args: print('previous-api')
elif 'exec' in args and 'api' in args: sys.exit(0 if os.environ['HEALTHY']=='true' else 1)
''')
        docker.chmod(0o755)
        sleep = binary / 'sleep'
        sleep.write_text('#!/bin/sh\nexit 0\n')
        sleep.chmod(0o755)
        log = root / 'docker.log'
        env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
               'FINDBACK_DEPLOY_ROOT': str(root), 'HEALTHY': str(healthy).lower(), 'DOCKER_LOG': str(log)}
        result = subprocess.run(['bash', str(script), image, str(release)], env=env, capture_output=True, text=True, timeout=15)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        assert 'PROVIDER_SECRET=keep-this-value' in envfile.read_text()
        assert envfile.stat().st_mode & 0o777 == 0o640
        assert (envfile.stat().st_uid, envfile.stat().st_gid) == original_owner
        if healthy:
            assert result.returncode == 0, result.stderr
            assert 'BACKEND_IMAGE=' + image in envfile.read_text()
            assert (root / 'infrastructure/Caddyfile').read_text() == 'new Caddyfile'
            assert not any('never' in call for call in calls)
        else:
            assert result.returncode != 0
            assert 'BACKEND_IMAGE=' not in envfile.read_text()
            assert (root / 'infrastructure/Caddyfile').read_text() == 'old Caddyfile'
            assert (root / 'infrastructure/compose.production.yml').read_text() == 'old compose.production.yml'
            assert any('never' in call for call in calls), 'Previous image must be restored without pulling'
print('PASS: 2 deployment cases; healthy image persists; failed readiness rolls back image/config; provider secret unchanged')
