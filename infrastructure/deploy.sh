#!/usr/bin/env bash
set -euo pipefail
image=${1:?Supply the immutable ECR image URI}
release=${2:?Supply the release directory}
root=${FINDBACK_DEPLOY_ROOT:-/opt/findback}
region=${AWS_REGION:-eu-west-1}
[[ "$image" =~ ^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9_-]+:[a-zA-Z0-9_-]+$ ]]
cd "$root"
exec 9>"$root/deploy.lock"
flock -n 9 || { echo 'Another deployment is active'; exit 1; }
registry=${image%%/*}
docker run --rm -e AWS_REGION="$region" public.ecr.aws/aws-cli/aws-cli:latest ecr get-login-password --region "$region" |
  docker login --username AWS --password-stdin "$registry"
docker pull "$image"
compose=(docker compose --env-file "$root/.env" -f "$root/infrastructure/compose.production.yml")
previous_container=$("${compose[@]}" ps -q api)
previous_image=$(docker inspect "$previous_container" --format '{{.Config.Image}}')
backup=$(mktemp -d "$root/releases/rollback.XXXXXX")
cp infrastructure/compose.production.yml infrastructure/Caddyfile "$backup/"
rollback() {
  trap - ERR
  echo 'Deployment failed; restoring previous image and configuration'
  cp "$backup/compose.production.yml" "$backup/Caddyfile" infrastructure/
  export BACKEND_IMAGE="$previous_image"
  "${compose[@]}" up -d --build --pull never
  "${compose[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
  exit 1
}
trap rollback ERR
cp "$release/infrastructure/compose.production.yml" "$release/infrastructure/Caddyfile" infrastructure/
export BACKEND_IMAGE="$image"
"${compose[@]}" up -d --build --pull always
"${compose[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
healthy=false
for _ in $(seq 1 30); do
  if "${compose[@]}" exec -T api python -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=5)); assert d["db"]==d["redis"]=="ok"'; then
    healthy=true
    break
  fi
  sleep 2
done
[[ "$healthy" == true ]]
# Persist only after readiness succeeds; database/provider secrets stay untouched.
python3 - "$root/.env" "$image" <<'PY'
import os,sys,tempfile
from pathlib import Path
path=Path(sys.argv[1]); lines=[line for line in path.read_text().splitlines() if not line.startswith('BACKEND_IMAGE=')]
original=path.stat()
fd,tmp=tempfile.mkstemp(dir=path.parent)
os.fchmod(fd,original.st_mode & 0o777)
os.fchown(fd,original.st_uid,original.st_gid)
with os.fdopen(fd,'w') as handle: handle.write('\n'.join(lines)+"\nBACKEND_IMAGE="+sys.argv[2]+"\n")
os.replace(tmp,path)
PY
trap - ERR
echo "Deployment healthy: $image"
