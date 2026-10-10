"""Check deployment gates and production image ownership without secrets."""
from pathlib import Path
import subprocess
import yaml

root = Path(__file__).resolve().parents[1]
workflow = yaml.load((root / '.github/workflows/ci-cd.yml').read_text(), Loader=yaml.BaseLoader)
assert set(workflow['on']) == {'push', 'pull_request', 'workflow_dispatch'}
jobs = workflow['jobs']; deploy = jobs['deploy']
assert set(deploy['needs']) == {'backend', 'mobile'}
assert "refs/heads/master" in deploy['if'] and 'pull_request' not in deploy['if']
assert deploy['permissions']['id-token'] == 'write'
assert deploy['concurrency']['cancel-in-progress'] == 'false'
assert jobs['backend']['services']['postgres']['env']['POSTGRES_DB'] == 'findback_test'
step = next(s for s in jobs['backend']['steps'] if s.get('run') == 'pytest')
assert step['env']['TEST_DATABASE_URL'].endswith('/findback_test')
commands = '\n'.join(s.get('run', '') for s in deploy['steps'])
assert 'docker push' in commands and 'ssm send-command' in commands
assert 'GITHUB_SHA' in commands and 'raw.githubusercontent.com' in commands
assert 'AWS_ACCESS_KEY_ID' not in str(deploy)
trust = (root / 'infrastructure/cicd.tf').read_text()
variables = (root / 'infrastructure/variables.tf').read_text()
assert '"token.actions.githubusercontent.com:sub" = "${var.github_oidc_subject_prefix}:ref:refs/heads/${var.deploy_branch}"' in trust
assert 'repo:Moaz-Elgendy@297489873/FindBack@1393710703' in variables
assert 'StringEquals' in trust and 'StringLike' not in trust
production = (root / 'infrastructure/compose.production.yml').read_text()
assert 'BACKEND_IMAGE' in production and 'build:' not in production
script = (root / 'infrastructure/deploy.sh').read_text()
assert '--build --pull always' in script and 'trap rollback ERR' in script
assert '--pull never' in script and 'flock -n' in script
subprocess.run(['bash', '-n', str(root / 'infrastructure/deploy.sh')], check=True)
print('PASS: both test gates; safe test DB; master-only deployment; OIDC; immutable image; SSM; lock and rollback; published image compose')
