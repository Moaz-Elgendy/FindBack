# CI/CD verification

The app uses https://findback.duckdns.org and Supabase; the local backend is not required. Mobile-data-only phone testing remains unverified.

## Deployment path

`.github/workflows/ci.yml` runs backend tests, Flutter analysis, and Flutter tests on pushes and pull requests. After both jobs pass, a `master` push builds and publishes an immutable commit-SHA backend image to ECR in `eu-west-1`, then deploys it through SSM to EC2 `i-0eb2c28f2a9070163`. Manual workflow runs deploy only on `master`.

API, worker, and outbox use the same image. Production deployments are serialized, check API database/Redis readiness, and roll back the prior image/configuration on failure. Compose executes `up -d --build --pull always` using the already-built ECR image. Private `.env` credentials are preserved. Schema migrations are not automated.

ECR, EC2 instance profile, and GitHub OIDC deployment role were applied with Terraform: **8 added, 1 changed, 0 destroyed**. EC2 was not replaced. The final convergence plan reported no changes. The existing AWS GitHub OIDC provider was reused. GitHub repository variables are configured; no static AWS secrets were added.

## Commands and observed results

- `SUPABASE_URL='' TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test REDIS_URL=redis://localhost:6379/0 .venv/bin/python -m pytest -q` from `backend`: **800 passed, 1 skipped, 24 warnings**. Only the dedicated local test database was used.
- `flutter test --reporter expanded` from `mobile`: **148 passed, 1 skipped**.
- `flutter analyze` from `mobile`: **No issues found**.
- `python infrastructure/test_deploy.py`: **2 deployment cases passed**, including failed-readiness rollback and preservation of private configuration.
- `backend/.venv/bin/python infrastructure/verify_cicd.py`: **PASS**, test gates, test database, master-only deployment, OIDC, SHA image, SSM, lock, rollback, and Compose image configuration.
- `bash -n infrastructure/deploy.sh`: exit **0**.
- `/tmp/actionlint .github/workflows/ci.yml`: exit **0**, no workflow errors.
- AWS IAM policy simulation: `ecr:PutImage` on this repository and `ssm:SendCommand` on this instance/document returned **allowed**.
- `terraform -chdir=infrastructure validate`: **Success**.
- `curl --fail --silent --show-error https://findback.duckdns.org/health`: API, database, and Redis reported **ok** before the deployment smoke check.

Two stale backend test harnesses were corrected: the fake ADB now implements queries used by the existing launcher; the missing JWT subject case invokes the actual authentication dependency and still requires HTTP 401. Production authentication code was not changed in this phase.

## Real ECR/SSM deployment

After the test gates passed locally, `docker build` and `docker push` succeeded for the uncommitted-tree bootstrap image `890608336467.dkr.ecr.eu-west-1.amazonaws.com/findback-backend:bootstrap-1791397410`. Its digest is `sha256:2bbe27bf58914994bfa8060fa4f5674b6e215f5fe7c6df58f3a2d61cfb7bddbe`. This bootstrap tag does not represent a Git commit; normal workflow tags are commit SHAs.

Current deployment files were uploaded through SSH for this initial smoke check because they are not on GitHub yet. `aws ssm send-command` invoked the real deployment script; `get-command-invocation` returned **Success**. EC2 pulled the image through its instance role. Compose recreated API, worker, and outbox, and readiness passed. A subsequent SSH `docker compose ... ps --format json` showed all three running that ECR image, API healthy, and Redis healthy. `stat` confirmed `.env` remained `600 ubuntu:ubuntu`. Public HTTPS `/health` again returned API/database/Redis **ok**. No schema migration was performed.

Rollback was tested with isolated Docker doubles; a deliberate production failure was not induced. GitHub OIDC issuance and raw-GitHub release downloads are not verified by the initial upload/SSM smoke check.

## Activation

The workflow is prepared locally. An actual GitHub Actions/OIDC workflow run is **NOT RUN**: these changes have not been committed or pushed. Include the workflow and infrastructure files in the commit, then push `master`. No Git push was performed on the user's behalf.
