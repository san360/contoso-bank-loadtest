"""Build once, test an exact source SHA, then promote the tested staging slot."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from tools.loadtest_gate import check_demo_contract, evaluate, run_snapshot

from .common import (
    DEMO_BRANCH, TEST_ID, DemoError, GitLab, ROOT, azure, contract_digest,
    http_json, https_origin, read_json, required, run, sha, write_json,
)
from . import monitoring

EVIDENCE = ROOT / "demo-evidence"
DIST = ROOT / "dist"
EXPECTED_CONFIG = {
    "enable_cache": True, "db_pool_size": 64, "db_query_ms": 25,
    "enable_transfer_lock": False, "enable_n_plus_one": False,
    "fraud_check_parallel": True, "statement_cpu_rounds": 1500, "error_rate_percent": 0,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def feature_name(source: str) -> str:
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "FEATURE" for target in node.targets
        ):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise DemoError("The feature module must declare a constant FEATURE name")


def build(source_sha: str, pipeline_id: str) -> dict:
    source_sha = sha(source_sha)
    result = subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=zip", f"{source_sha}:src"],
        capture_output=True, timeout=120,
    )
    if result.returncode:
        raise DemoError(f"git archive failed: {result.stderr.decode(errors='replace')}")
    DIST.mkdir(exist_ok=True)
    candidate = DIST / "candidate.zip"
    candidate.write_bytes(result.stdout)
    with zipfile.ZipFile(candidate, "a", compression=zipfile.ZIP_DEFLATED) as package:
        if "build-info.json" in package.namelist():
            raise DemoError("Generated build-info.json must not be checked into source control")
        info = zipfile.ZipInfo("build-info.json", (2020, 1, 1, 0, 0, 0))
        package.writestr(info, json.dumps({"source_sha": source_sha, "pipeline_id": pipeline_id}))
        feature = feature_name(package.read("transaction_enrichment.py").decode("utf-8"))
    manifest = {
        "source_sha": source_sha,
        "pipeline_id": pipeline_id,
        "feature": feature,
        "artifact_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "contract_sha256": contract_digest(),
    }
    write_json(DIST / "candidate.json", manifest)
    print(json.dumps(manifest, indent=2))
    return manifest


def verify_candidate(source_sha: str, pipeline_id: str, folder: Path = DIST) -> dict:
    manifest = read_json(folder / "candidate.json")
    if manifest.get("source_sha") != sha(source_sha) or str(manifest.get("pipeline_id")) != pipeline_id:
        raise DemoError("Artifact source SHA or producing pipeline does not match this candidate")
    candidate = folder / "candidate.zip"
    if hashlib.sha256(candidate.read_bytes()).hexdigest() != manifest.get("artifact_sha256"):
        raise DemoError("Candidate ZIP hash differs from its build manifest")
    with zipfile.ZipFile(candidate) as package:
        info = json.loads(package.read("build-info.json"))
    if info != {"source_sha": source_sha, "pipeline_id": pipeline_id}:
        raise DemoError("Embedded artifact version does not match its build manifest")
    if manifest.get("contract_sha256") != contract_digest():
        raise DemoError("Load profile or performance criteria changed after the build")
    return manifest


def wait_version(origin: str, source_sha: str, pipeline_id: str, *, seconds: int = 900) -> dict:
    origin = https_origin(origin)
    deadline = time.monotonic() + seconds
    last = "No response"
    while time.monotonic() < deadline:
        try:
            version = http_json(origin + "/version")
        except urllib.error.HTTPError as error:
            if error.code not in {404, 409, 429, 500, 502, 503, 504}:
                raise
            last = f"HTTP {error.code}"
        except urllib.error.URLError as error:
            last = str(error.reason)
        except json.JSONDecodeError:
            last = "The starting app has not returned a JSON version document yet"
        else:
            if (
                isinstance(version, dict) and version.get("source_sha") == source_sha
                and str(version.get("pipeline_id")) == pipeline_id
            ):
                return version
            last = f"Different candidate: {version.get('source_sha')!r}" if isinstance(version, dict) else "Malformed version document"
        time.sleep(10)
    raise DemoError(f"{origin} did not serve the exact candidate within {seconds}s: {last}")


def functional_digest(payload: dict) -> str:
    if payload.get("count") != 40 or len(payload.get("items", [])) != 40:
        raise DemoError("Warm-up did not return the expected deterministic 40-row page")
    rows = []
    for item in payload["items"]:
        merchant = item.get("merchant")
        if not isinstance(merchant, dict) or merchant.get("name") != item.get("description"):
            raise DemoError("The deployed merchant enrichment feature is missing or malformed")
        if not isinstance(merchant.get("category"), str):
            raise DemoError("The deployed category enrichment feature is missing")
        rows.append({key: value for key, value in item.items() if key != "booked_at"})
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


class Context:
    def __init__(self):
        self.subscription = required("AZURE_SUBSCRIPTION_ID")
        self.tenant = required("AZURE_TENANT_ID")
        self.group = required("AZURE_RESOURCE_GROUP")
        self.app = required("APP_NAME")
        self.staging = https_origin(required("STAGING_URL"))
        self.production = https_origin(required("PRODUCTION_URL"))
        self.load = required("LOAD_TEST_NAME")
        self.load_id = required("LOAD_TEST_RESOURCE_ID")
        self.staging_id = required("STAGING_RESOURCE_ID")
        self.plan_id = required("APP_SERVICE_PLAN_ID")
        self.workspace = required("LOG_WORKSPACE_ID")
        self.source_sha = sha(required("CI_COMMIT_SHA"))
        self.pipeline_id = required("CI_PIPELINE_ID")
        self.job_id = required("CI_JOB_ID")
        self.client = GitLab(
            required("CI_SERVER_URL"), required("GITLAB_BOT_TOKEN"), required("CI_PROJECT_ID")
        )

    def load_cli(self, arguments: list[str], *, timeout=180) -> str:
        return azure(
            ["load", *arguments, "--load-test-resource", self.load, "--resource-group", self.group],
            self.subscription, timeout=timeout,
        )

    def main_sha(self) -> str:
        return self.client.get("/repository/branches/main")["commit"]["id"]

    def login(self) -> None:
        run([
            "az", "login", "--identity", "--client-id", required("AZURE_CLIENT_ID"),
            "--allow-no-subscriptions", "--only-show-errors", "-o", "none",
        ], timeout=120)
        account = json.loads(run(["az", "account", "show", "--only-show-errors", "-o", "json"]))
        if account["id"] != self.subscription or account["tenantId"] != self.tenant:
            raise DemoError("Runner's isolated CLI login did not select the expected managed-identity subscription")
        write_json(EVIDENCE / "azure-auth.json", {
            "subscription_id": account["id"], "tenant_id": account["tenantId"],
            "managed_identity_client_id": required("AZURE_CLIENT_ID"),
        })


def check_mr(context: Context) -> dict:
    iid = required("CI_MERGE_REQUEST_IID")
    deadline = time.monotonic() + 60
    while True:
        mr = context.client.get(f"/merge_requests/{iid}")
        branch = context.client.get(
            f"/repository/branches/{urllib.parse.quote(DEMO_BRANCH, safe='')}"
        )
        if (
            mr["state"] != "opened" or mr["source_branch"] != DEMO_BRANCH
            or mr["target_branch"] != "main" or branch["commit"]["id"] != context.source_sha
            or str(mr["source_project_id"]) != str(context.client.project_id)
            or str(mr["target_project_id"]) != str(context.client.project_id)
        ):
            raise DemoError("Only the current, same-project, protected demo MR may use staging")
        if mr["sha"] == context.source_sha:
            break
        if time.monotonic() >= deadline:
            raise DemoError("GitLab's MR diff cache did not catch up with the verified source branch")
        time.sleep(2)
    opened = context.client.get("/merge_requests?state=opened&per_page=100")
    if len(opened) != 1 or str(opened[0]["iid"]) != iid:
        raise DemoError("The isolated demo requires exactly one open candidate MR")
    return mr


def render_test(staging: str, folder: Path) -> None:
    source = ROOT / "loadtests" / "gitlab-demo"
    folder.mkdir(parents=True, exist_ok=True)
    profile = read_json(source / "transactions.json")
    request = profile["scenarios"]["transactions"]["requests"][0]
    if request["endpoint"] != "https://TARGET_HOST/api/accounts/ACC-1001/transactions":
        raise DemoError("The checked-in test target template has changed")
    request["endpoint"] = https_origin(staging) + "/api/accounts/ACC-1001/transactions"
    write_json(folder / "transactions.json", profile)
    shutil.copyfile(source / "transactions.yaml", folder / "transactions.yaml")


def wait_load_run(context: Context, run_id: str, *, seconds=1200, settle_seconds=300):
    deadline = time.monotonic() + seconds
    settle_deadline = None
    while time.monotonic() < deadline:
        payload = json.loads(context.load_cli([
            "test-run", "show", "--test-run-id", run_id, "-o", "json",
        ]))
        write_json(EVIDENCE / "run.json", run_snapshot(payload))
        verdict = evaluate(payload)
        print(f"ALT {run_id}: {payload.get('status')} / {verdict.outcome}", flush=True)
        if verdict.outcome != "pending":
            return payload, verdict
        if payload.get("status") == "DONE":
            if settle_deadline is None:
                settle_deadline = time.monotonic() + settle_seconds
            elif time.monotonic() >= settle_deadline:
                raise DemoError("ALT DONE but verdict/criterion/statistic evidence did not settle")
        time.sleep(10)
    raise DemoError("ALT run did not reach a judged terminal state within the bounded timeout")


def gate(context: Context) -> int:
    EVIDENCE.mkdir(exist_ok=True)
    summary = {
        "outcome": "infrastructure_failed",
        "reason": "Gate did not complete",
        "source_sha": context.source_sha,
        "pipeline_id": context.pipeline_id,
        "job_id": context.job_id,
        "mr_iid": required("CI_MERGE_REQUEST_IID"),
        "started_at": now(),
    }
    write_json(EVIDENCE / "summary.json", summary)
    run_id = f"gl-{context.pipeline_id}-{context.job_id}-{context.source_sha[:8]}"
    run_started = False
    final_payload = None
    try:
        manifest = verify_candidate(context.source_sha, context.pipeline_id)
        if manifest["feature"] != "merchant-category-v1":
            raise DemoError("The candidate must contain the actual merchant/category feature")
        summary.update(manifest)
        check_mr(context)
        main_sha = sha(context.main_sha())
        run(["git", "merge-base", "--is-ancestor", main_sha, context.source_sha])
        summary["base_main_sha"] = main_sha
        context.login()
        azure([
            "webapp", "deploy", "--resource-group", context.group, "--name", context.app,
            "--slot", "staging", "--src-path", str(DIST / "candidate.zip"), "--type", "zip",
            "--clean", "true", "--restart", "true", "--async", "true",
            "--track-status", "false", "-o", "none",
        ], context.subscription, timeout=900)
        version = wait_version(context.staging, context.source_sha, context.pipeline_id)
        write_json(EVIDENCE / "version.json", version)
        headers = {"X-Demo-Admin-Token": required("DEMO_ADMIN_TOKEN")}
        config = http_json(context.staging + "/admin/config", headers=headers)["config"]
        if any(config.get(key) != value for key, value in EXPECTED_CONFIG.items()):
            raise DemoError("Stable optimized app settings differ from the fixed demo contract")
        write_json(EVIDENCE / "app-config.json", config)
        http_json(context.staging + "/admin/reset", payload={}, headers=headers)
        for _ in range(3):
            page = http_json(context.staging + "/api/accounts/ACC-1001/transactions?page_size=40")
            summary["functional_sha256"] = functional_digest(page)
        check_mr(context)
        if context.main_sha() != main_sha:
            raise DemoError("main drifted during candidate preparation")
        render_test(context.staging, EVIDENCE / "input")
        context.load_cli([
            "test", "create", "--test-id", TEST_ID, "--load-test-config-file",
            str(EVIDENCE / "input" / "transactions.yaml"), "-o", "none",
        ], timeout=300)
        monitoring.configure(context, EVIDENCE)
        context.load_cli([
            "test-run", "create", "--test-id", TEST_ID, "--test-run-id", run_id, "--no-wait",
            "--description", f"GitLab pipeline {context.pipeline_id}; source {context.source_sha}",
            "-o", "none",
        ], timeout=180)
        run_started = True
        summary["alt_run_id"] = run_id
        write_json(EVIDENCE / "summary.json", summary)
        payload, verdict = wait_load_run(context, run_id)
        final_payload = payload
        if verdict.outcome in {"passed", "performance_failed"}:
            check_demo_contract(verdict)
        summary.update(verdict.as_dict())
        summary["alt_start"] = payload.get("startDateTime")
        summary["alt_end"] = payload.get("endDateTime")
        wait_version(context.staging, context.source_sha, context.pipeline_id, seconds=60)
        check_mr(context)
        if context.main_sha() != main_sha:
            raise DemoError("main drifted while the candidate was under load")
    except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        summary.update(outcome="infrastructure_failed", reason=str(error))
        raise
    finally:
        if run_started:
            try:
                context.load_cli([
                    "test-run", "download-files", "--test-run-id", run_id,
                    "--path", str(EVIDENCE / "downloads"), "--result", "--log", "--report",
                    "--force", "-o", "none",
                ], timeout=300)
            except (DemoError, subprocess.TimeoutExpired) as error:
                summary.update(outcome="infrastructure_failed", reason=f"Evidence download failed: {error}")
            if final_payload is not None:
                try:
                    summary["monitoring"] = monitoring.collect(context, final_payload, EVIDENCE)
                except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
                    summary["monitoring"] = {"status": "error", "reason": str(error)}
                    print(f"Monitoring evidence unavailable (client verdict unchanged): {error}", file=sys.stderr)
        summary["finished_at"] = now()
        write_json(EVIDENCE / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return {"passed": 0, "performance_failed": 1}.get(summary["outcome"], 2)


def verified_green(context: Context, job_id: str, mr_iid: str) -> dict:
    job = context.client.get(f"/jobs/{int(job_id)}")
    if job["name"] != "performance_gate" or job["status"] != "success":
        raise DemoError("Promotion requires the successful locked performance_gate job")
    producing = context.client.get(f"/pipelines/{job['pipeline']['id']}")
    if (
        producing["sha"] != context.source_sha or producing["status"] != "success"
        or producing["source"] != "merge_request_event"
    ):
        raise DemoError("The approved MR pipeline is not successful for this exact commit")
    body = context.client.request(
        "GET", f"{context.client.project}/jobs/{int(job_id)}/artifacts/demo-evidence/summary.json",
        raw=True,
    )
    proof = json.loads(body)
    if (
        proof.get("outcome") != "passed" or proof.get("source_sha") != context.source_sha
        or str(proof.get("job_id")) != str(job_id)
        or str(proof.get("pipeline_id")) != str(producing["id"])
        or str(proof.get("mr_iid")) != str(mr_iid)
        or proof.get("contract_sha256") != contract_digest()
    ):
        raise DemoError("Approved gate evidence does not match this promotion")
    mr = context.client.get(f"/merge_requests/{int(mr_iid)}")
    head_pipeline = mr.get("head_pipeline") or {}
    if (
        mr["state"] != "merged" or mr["sha"] != context.source_sha
        or str(head_pipeline.get("id")) != str(producing["id"])
        or head_pipeline.get("status") != "success"
    ):
        raise DemoError("The merged MR does not point to the approved latest green pipeline")
    return proof


def promote(context: Context) -> int:
    proof = verified_green(context, required("DEMO_GATE_JOB_ID"), required("DEMO_MR_IID"))
    expected = sha(required("DEMO_PROMOTE_SHA"))
    if context.source_sha != expected or required("CI_COMMIT_BRANCH") != "main":
        raise DemoError("Only the fast-forwarded, tested main commit may be promoted")
    if context.main_sha() != expected:
        raise DemoError("main advanced after this promotion pipeline was created")
    if context.client.get("/merge_requests?state=opened&per_page=100"):
        raise DemoError("Another candidate is open; promotion is not exclusive")
    context.login()
    payload = json.loads(context.load_cli([
        "test-run", "show", "--test-run-id", proof["alt_run_id"], "-o", "json",
    ]))
    verdict = evaluate(payload)
    check_demo_contract(verdict)
    if verdict.outcome != "passed":
        raise DemoError("A fresh ALT read no longer proves a settled pass")
    wait_version(context.staging, expected, str(proof["pipeline_id"]), seconds=60)
    if context.main_sha() != expected:
        raise DemoError("main drifted immediately before the slot swap")
    azure([
        "webapp", "deployment", "slot", "swap", "--resource-group", context.group,
        "--name", context.app, "--slot", "staging", "--target-slot", "production",
        "-o", "none",
    ], context.subscription, timeout=900)
    version = wait_version(context.production, expected, str(proof["pipeline_id"]))
    if context.main_sha() != expected:
        raise DemoError("main changed during promotion; investigate the isolated demo before continuing")
    write_json(EVIDENCE / "promotion.json", {
        "outcome": "promoted", "source_sha": expected, "pipeline_id": context.pipeline_id,
        "gate_pipeline_id": proof["pipeline_id"], "alt_run_id": proof["alt_run_id"],
        "artifact_sha256": proof["artifact_sha256"], "production_url": context.production,
        "version": version, "finished_at": now(),
    })
    print(f"Promoted the tested slot, without rebuilding: {expected} -> {context.production}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["build", "gate", "promote"])
    parser.add_argument("--sha")
    parser.add_argument("--pipeline-id")
    args = parser.parse_args()
    try:
        if args.operation == "build":
            build(args.sha or required("CI_COMMIT_SHA"), args.pipeline_id or required("CI_PIPELINE_ID"))
            return 0
        return {"gate": gate, "promote": promote}[args.operation](Context())
    except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
