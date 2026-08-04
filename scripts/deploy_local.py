"""Local CI/CD for trading-lab — replaces GitHub Actions (deleted 2026-08-04).

Usage (from repo root):

    python scripts/deploy_local.py            # CI + tofu plan (no changes)
    python scripts/deploy_local.py --apply    # CI + plan + tofu apply
    python scripts/deploy_local.py --build    # docker build+push new image first
    python scripts/deploy_local.py --skip-ci  # infra-only: skip lint/tests

Flow mirrors the deleted deploy.yml: ruff + pytest → (optional image build)
→ tofu fmt/init/validate/plan → (optional) apply → /health smoke.

Image build needs local Docker. Without it, infra deploys reuse the current
ECR image tag (resolved automatically), which is right for infra-only patches.
Code deploys require --build (or push an image another way and pass --image-tag).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
APP = "trading-lab"
REGION = "us-east-1"


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True,
        capture: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    out = subprocess.run(
        cmd, cwd=cwd or ROOT, env=env,
        capture_output=capture, text=capture or None,
        shell=(os.name == "nt"),
    )
    if check and out.returncode != 0:
        print(f"FAILED ({out.returncode}): {' '.join(cmd)}")
        sys.exit(out.returncode)
    return out


def ci() -> None:
    print("\n== CI: lint + format + tests ==")
    run(["uv", "run", "ruff", "check", "src", "api", "tests"])
    run(["uv", "run", "ruff", "format", "--check", "src", "api"])
    run(["uv", "run", "python", "-m", "pytest", "-q"])


def ecr_registry() -> str:
    out = run(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
              capture=True)
    account = out.stdout.strip()
    return f"{account}.dkr.ecr.{REGION}.amazonaws.com"


def current_ecr_tag() -> str | None:
    out = run([
        "aws", "ecr", "describe-images", "--repository-name", APP, "--region", REGION,
        "--query", "sort_by(imageDetails,& imagePushedAt)[-1].imageTags[0]",
        "--output", "text",
    ], check=False, capture=True)
    tag = out.stdout.strip() if out.returncode == 0 else ""
    return tag if tag and tag != "None" else None


def build_and_push() -> str:
    if shutil.which("docker") is None:
        print("ERROR: docker not on PATH. Infra-only deploys work without it; "
              "code deploys need Docker (or push an image and use --image-tag).")
        sys.exit(1)
    tag = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    registry = ecr_registry()
    image = f"{registry}/{APP}:{tag}"
    print(f"\n== Image build+push: {image} ==")
    run(["aws", "ecr", "get-login-password", "--region", REGION], capture=True)
    login = subprocess.run(
        ["aws", "ecr", "get-login-password", "--region", REGION],
        capture_output=True, text=True, shell=(os.name == "nt"),
    )
    pw = subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", registry],
        input=login.stdout, text=True, shell=(os.name == "nt"),
    )
    if pw.returncode != 0:
        print("FAILED: docker login")
        sys.exit(pw.returncode)
    run(["docker", "build", "-t", image, "."])
    run(["docker", "push", image])
    return tag


def tofu(args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    return run(["tofu", *args], cwd=INFRA, capture=capture)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="tofu apply after plan")
    p.add_argument("--destroy", action="store_true", help="tofu destroy (careful)")
    p.add_argument("--build", action="store_true", help="docker build+push new image first")
    p.add_argument("--image-tag", default="", help="explicit ECR tag to deploy")
    p.add_argument("--skip-ci", action="store_true", help="skip ruff/pytest")
    args = p.parse_args()

    if not args.skip_ci:
        ci()

    if args.build:
        tag = build_and_push()
    elif args.image_tag:
        tag = args.image_tag
    else:
        tag = current_ecr_tag()
        if not tag:
            print("ERROR: no ECR image found. Run with --build first.")
            return 1
        print(f"\nReusing current ECR tag: {tag} (infra-only deploy; --build to ship code)")

    image_uri = f"{ecr_registry()}/{APP}:{tag}"

    print("\n== OpenTofu ==")
    tofu(["fmt", "-check", "-recursive"])
    tofu(["init", "-input=false"])
    tofu(["validate"])
    tfvars = ["-var", f"image_uri={image_uri}", "-var", f"image_tag={tag}"]
    plan_args = ["plan", "-input=false", "-out=tfplan", *tfvars]
    if args.destroy:
        plan_args.insert(1, "-destroy")
    tofu(plan_args)

    if not args.apply and not args.destroy:
        print("\nPlan only. Re-run with --apply to apply (after reviewing the plan above).")
        return 0

    tofu(["apply", "-input=false", "tfplan"])
    (INFRA / "tfplan").unlink(missing_ok=True)

    out = tofu(["output", "-json"], capture=True)
    try:
        outputs = json.loads(out.stdout)
        url = (outputs.get("lambda_function_url") or {}).get("value")
    except Exception:  # noqa: BLE001
        url = None
    if url:
        print(f"\n== Health smoke: {url}health ==")
        run(["uv", "run", "python", "-c",
             f"import urllib.request,sys; r=urllib.request.urlopen('{url}health', timeout=30);"
             " print(r.status, r.read().decode()[:200]);"
             " sys.exit(0 if r.status==200 else 1)"],
            check=False)
    print("\nDEPLOY COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
