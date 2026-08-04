"""Pre-deploy gate for trading-lab (local-only CI/CD; no GitHub Actions).

Run before any `tofu apply` / `tofu destroy`:

    python scripts/pre_deploy_check.py

Prints: identity, tooling, backend reachability, current deployment state,
and a cost/delta note. Exit 0 = OK to proceed to plan; non-zero = stop.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

REGION = "us-east-1"
APP = "trading-lab"
SECRET_ID = "trading-lab-vendor-keys"
STATE_BUCKET = "platform-tfstate-b667becb"
STATE_KEY = "apps/trading-lab/dev/terraform.tfstate"

FAILURES: list[str] = []


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                             shell=(os.name == "nt"))
        text = out.stdout.strip()
        if out.returncode != 0 and out.stderr.strip():
            text = f"{text}\n{out.stderr.strip()}".strip()
        return out.returncode, text
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "OK " if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print(f"== pre-deploy check: {APP} (region {REGION}) ==\n")

    # 1) Tooling
    check("tofu on PATH", shutil.which("tofu") is not None,
          shutil.which("tofu") or "install OpenTofu")
    check("aws CLI on PATH", shutil.which("aws") is not None,
          shutil.which("aws") or "install AWS CLI v2")
    if shutil.which("tofu"):
        rc, out = run(["tofu", "version"])
        print(f"       {out.splitlines()[0] if out else 'version unknown'}")

    # 2) AWS identity (workstation admin creds expected)
    rc, out = run(["aws", "sts", "get-caller-identity", "--output", "json"])
    if rc == 0:
        try:
            ident = json.loads(out)
            check("AWS identity", True,
                  f"account={ident.get('Account')} arn={ident.get('Arn')}")
        except Exception:  # noqa: BLE001
            check("AWS identity", False, "unparseable sts output")
    else:
        check("AWS identity", False, out[:200])

    # 3) Backend state reachable (read-only)
    rc, out = run([
        "aws", "s3api", "head-object",
        "--bucket", STATE_BUCKET, "--key", STATE_KEY, "--region", REGION,
    ])
    check("tofu state object", rc == 0, f"s3://{STATE_BUCKET}/{STATE_KEY}")

    # 4) Vendor secrets shell exists
    rc, out = run([
        "aws", "secretsmanager", "describe-secret",
        "--secret-id", SECRET_ID, "--region", REGION,
        "--query", "Name", "--output", "text",
    ])
    check("vendor secrets shell", rc == 0 and SECRET_ID in out, SECRET_ID)

    # 5) Current deployment snapshot
    rc, out = run([
        "aws", "lambda", "get-function-configuration",
        "--function-name", f"{APP}-worker", "--region", REGION,
        "--query", "ImageUri", "--output", "text",
    ])
    if rc == 0 and "ecr" in out:
        print(f"\nCurrent Lambda image: {out.strip()}")
    else:
        rc2, out2 = run([
            "aws", "lambda", "get-function",
            "--function-name", f"{APP}-worker", "--region", REGION,
            "--query", "Configuration.ImageConfigResponse.ImageUri", "--output", "text",
        ])
        if rc2 == 0 and "ecr" in out2:
            print(f"\nCurrent Lambda image: {out2.strip()}")
        else:
            print("\nCurrent Lambda image: (unresolved — ImageUri not in config API)")

    rc, out = run([
        "aws", "ecr", "describe-images",
        "--repository-name", APP, "--region", REGION,
        "--query", "sort_by(imageDetails,& imagePushedAt)[-1].imageTags[0]",
        "--output", "text",
    ])
    if rc == 0 and out and out != "None":
        print(f"Latest ECR tag:       {out.strip()}")

    # 6) Cost note
    print("""
Cost/delta note:
  - This stack: Lambda worker + EventBridge schedules + S3 journal + secrets.
  - No new billable resources unless tofu plan shows adds — read the plan.
  - Grafana module removed 2026-08-04 (was free-tier Grafana Cloud).
""")

    if FAILURES:
        print(f"PRE-DEPLOY FAILED ({len(FAILURES)}): {', '.join(FAILURES)}")
        return 1
    print("PRE-DEPLOY OK — proceed to: python scripts/deploy_local.py (plan), "
          "then --apply after explicit confirmation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
