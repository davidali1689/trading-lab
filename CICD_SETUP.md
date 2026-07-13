# CI/CD setup (trading-lab)

Bootstrapped from cicd-templates @v1.0.1.

## GitHub

1. Create Environment **dev** (optional: required reviewers for apply).
2. Repo secret **AWS_ROLE_ARN** = `aws-cicd` output `github_actions_app_deploy_role_arn`
3. Repo secret **AWS_APPLY_ROLE_ARN** = `aws-cicd` output `github_actions_app_deploy_apply_role_arn` (optional but recommended)
4. Optional var **ECR_REGISTRY** = `ACCOUNT.dkr.ecr.us-east-1.amazonaws.com`
5. Ensure this repo is listed in `github_actions_deploy_repos` (`aws-cicd`).
   State keys: `apps/<app-name>/dev|feat-<slug>/terraform.tfstate`.

## Layout expected

- `api/` FastAPI app
- `tests/`
- `infra/` OpenTofu (lambda-container)
- `Dockerfile` (Lambda Web Adapter)
- `pyproject.toml` + `uv.lock`

## Pin

Reusable workflows: `davidali1689/cicd-templates/...@v1.0.1`
