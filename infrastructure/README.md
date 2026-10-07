# FindBack EC2 infrastructure

Single Ubuntu 24.04 x86 EC2 instance in `eu-west-1`, with a dedicated VPC, public subnet, internet gateway, route table, security group, NACL, SSH key pair, encrypted gp3 root volume, and Elastic IP. Docker and Docker Compose are installed by cloud-init. This phase does not deploy the application or migrate PostgreSQL.

The account reported an active FREE plan and `c7i-flex.large` as free-tier eligible when checked. Eligibility is not a promise of zero charges: EC2, EBS, and the public IPv4 address depend on account credits/limits. No NAT gateway or load balancer is created.

## Configuration and access

All deployment settings live in `variables.tf`. Copy `terraform.tfvars.example` to `terraform.tfvars` for another deployment; replace the example SSH CIDR and public key. Keep private keys outside this repository. The actual local `terraform.tfvars`, Terraform state, and plans are ignored; `.terraform.lock.hcl` should be committed.

Current resources:

- EC2: `i-0eb2c28f2a9070163`
- Elastic IP: `52.210.150.132`
- Region: `eu-west-1`
- Instance: `c7i-flex.large`, 2 vCPUs, 4 GB RAM; encrypted 30 GB gp3 disk

The repository's ignored `.env` now has `VM_HOST`, `VM_SSH_USER`, `VM_SSH_KEY_PATH`, `AWS_REGION`, and `AWS_INSTANCE_ID`. Existing database/authentication settings remain unchanged.

```bash
ssh -i ~/.local/state/findback/ec2/findback-ec2 ubuntu@52.210.150.132
```

The private key is local at `~/.local/state/findback/ec2/findback-ec2`; keep a secure backup. A Terraform state backup is at `~/.local/state/findback/ec2/terraform.tfstate.backup`. Terraform state is necessary to manage these resources; do not commit it or lose it.

SSH is restricted to the operator IP configured at creation. If your ISP changes your address, update `ssh_allowed_cidr`, review a new plan, and apply it. Only inbound TCP 22, 80, and 443 are permitted by the security group. The stateless NACL additionally permits TCP ephemeral return traffic; the security group blocks unsolicited traffic to those ports. Redis, PostgreSQL, and API port 8000 are not exposed. IMDSv2 tokens are mandatory.

## Commands and checks actually run

```bash
terraform -chdir=infrastructure init -input=false
terraform -chdir=infrastructure fmt
terraform -chdir=infrastructure validate
terraform -chdir=infrastructure plan -input=false -out=deployment.tfplan
python infrastructure/verify_plan.py
terraform -chdir=infrastructure fmt -check
terraform -chdir=infrastructure apply -input=false deployment.tfplan
terraform -chdir=infrastructure plan -input=false -detailed-exitcode
```

The initial apply created **11 resources**, changing or destroying none. The deployment-plan check passed: restricted SSH, web ports, encrypted disk, required IMDSv2 tokens, NACL return traffic, and a static IP. Validation passed. SSH connected; `cloud-init status` reported `done`; Docker and Compose reported installed versions.

The initial convergence check found that the provider reads an associated Elastic IP as `associate_public_ip_address=true`. Explicitly forcing that computed instance attribute false would replace the server on the next apply. The configuration instead disables automatic public-IP assignment on the subnet and lets the separate EIP association manage the instance address. No replacement was applied. The final convergence plan exited **0** and reported **no changes**. Docker daemon access also worked for the SSH user without sudo.

## Application deployment

The application and database migration are now deployed. See [deployment verification](../docs/PHASE9_DEPLOYMENT_VERIFICATION.md) for snapshot counts, privacy checks, real hosted tests, remaining limits, and rollback. The API is https://findback.duckdns.org; the phone APK was rebuilt for it. Supabase's existing Session-pooler connection is configured privately on EC2. Local writers are stopped; the original local database is retained.

On EC2, from `/opt/findback`, manage the application with:

```bash
docker compose --env-file .env -f infrastructure/compose.production.yml up -d --no-build
docker compose --env-file .env -f infrastructure/compose.production.yml ps
docker compose --env-file .env -f infrastructure/compose.production.yml logs --tail=50 api worker outbox
```

Do not run `terraform destroy` as rollback: it deletes the instance and its root disk and releases the IP. Application rollback should first point the client back at the preserved local backend/database. Infrastructure teardown requires a separate, deliberate decision.

## CI/CD

Pushes and pull requests run backend and Flutter checks. Only successful `master` pushes (or a manual run on `master`) deploy. The workflow builds the backend image once per commit SHA, publishes it to the immutable `findback-backend` ECR repository in `eu-west-1`, then uses SSM to update API, worker, and outbox on EC2. GitHub uses OIDC; EC2 uses its instance role. No static AWS credentials or SSH key are stored in GitHub.

Repository variables are configured: `AWS_REGION`, `ECR_REPOSITORY_URL`, `EC2_INSTANCE_ID`, `AWS_DEPLOY_ROLE_ARN`. `cicd.tf` manages the ECR repository, ten-image retention, and roles. It reuses this AWS account's existing GitHub OIDC provider.

`deploy.sh` serializes deployments, checks database/Redis readiness, and restores the previous image/configuration if the update fails. It persists `BACKEND_IMAGE` in the private server `.env` only after readiness succeeds. Compose uses the published image; `up -d --build --pull always` does not rebuild it on EC2. Database migrations remain a separate operation and must be backward compatible with rollback. See [verification](../docs/CICD_VERIFICATION.md).
