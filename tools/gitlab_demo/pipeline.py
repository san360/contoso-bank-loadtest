"""Non-Azure bookkeeping for the explicit az commands in .gitlab-ci.yml."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import zipfile

from tools.loadtest_gate import check_demo_contract, evaluate
from .common import (
    DEMO_BRANCH, DemoError, GitLab, ROOT, contract_digest, http_json, https_origin,
    read_json, required, run, sha, write_json,
)
from .monitoring import query_window, sample_count, selected_metrics

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
        raise DemoError("git archive could not package the exact source")
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
        "source_sha": source_sha, "pipeline_id": pipeline_id, "feature": feature,
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


def wait_version(origin: str, source_sha: str, pipeline_id: str, *, seconds=900) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            version = http_json(https_origin(origin) + "/version")
        except urllib.error.HTTPError as error:
            if error.code not in {404, 409, 429, 500, 502, 503, 504}:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        else:
            if isinstance(version, dict) and version.get("source_sha") == source_sha and str(version.get("pipeline_id")) == pipeline_id:
                return version
        time.sleep(10)
    raise DemoError(f"{origin} did not serve the exact candidate within {seconds}s")


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
        self.staging = https_origin(required("STAGING_URL"))
        self.production = https_origin(required("PRODUCTION_URL"))
        self.staging_id = required("STAGING_RESOURCE_ID")
        self.plan_id = required("APP_SERVICE_PLAN_ID")
        self.source_sha = sha(required("CI_COMMIT_SHA"))
        self.pipeline_id = required("CI_PIPELINE_ID")
        self.job_id = required("CI_JOB_ID")
        self.client = GitLab(required("CI_SERVER_URL"), required("GITLAB_BOT_TOKEN"), required("CI_PROJECT_ID"))

    def main_sha(self):
        return self.client.get("/repository/branches/main")["commit"]["id"]


def check_mr(context: Context) -> dict:
    iid = required("CI_MERGE_REQUEST_IID")
    deadline = time.monotonic() + 60
    while True:
        mr = context.client.get(f"/merge_requests/{iid}")
        branch = context.client.get(f"/repository/branches/{urllib.parse.quote(DEMO_BRANCH, safe='')}")
        if (
            mr["state"] != "opened" or mr["source_branch"] != DEMO_BRANCH
            or mr["target_branch"] != "main" or branch["commit"]["id"] != context.source_sha
            or str(mr["source_project_id"]) != str(context.client.project_id)
            or str(mr["target_project_id"]) != str(context.client.project_id)
        ):
            raise DemoError("Only the current same-project protected demo MR may use staging")
        if mr["sha"] == context.source_sha:
            break
        if time.monotonic() >= deadline:
            raise DemoError("GitLab MR diff cache did not catch up with the verified branch")
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
        raise DemoError("The checked-in target template changed")
    request["endpoint"] = https_origin(staging) + "/api/accounts/ACC-1001/transactions"
    write_json(folder / "transactions.json", profile)
    shutil.copyfile(source / "transactions.yaml", folder / "transactions.yaml")


def export(name: str, values: dict) -> None:
    (EVIDENCE / name).write_text("".join(f"export {key}={shlex.quote(str(value))}\n" for key, value in values.items()))


def prepare(context: Context):
    EVIDENCE.mkdir(exist_ok=True)
    summary = {
        "outcome": "infrastructure_failed", "reason": "Native CLI gate did not complete",
        "source_sha": context.source_sha, "pipeline_id": context.pipeline_id,
        "job_id": context.job_id, "mr_iid": required("CI_MERGE_REQUEST_IID"), "started_at": now(),
    }
    write_json(EVIDENCE / "summary.json", summary)
    manifest = verify_candidate(context.source_sha, context.pipeline_id)
    if manifest["feature"] != "merchant-category-v1":
        raise DemoError("Candidate must contain the actual merchant/category feature")
    check_mr(context)
    base = sha(context.main_sha())
    run(["git", "merge-base", "--is-ancestor", base, context.source_sha])
    run_id = f"gl-{context.pipeline_id}-{context.job_id}-{context.source_sha[:8]}"
    summary.update(manifest, base_main_sha=base, alt_run_id=run_id)
    write_json(EVIDENCE / "summary.json", summary)
    export("gate.env", {"ALT_RUN_ID": run_id})
    render_test(context.staging, EVIDENCE / "input")


def warm(context: Context):
    write_json(EVIDENCE / "version.json", wait_version(context.staging, context.source_sha, context.pipeline_id))
    headers = {"X-Demo-Admin-Token": required("DEMO_ADMIN_TOKEN")}
    config = http_json(context.staging + "/admin/config", headers=headers)["config"]
    if any(config.get(key) != value for key, value in EXPECTED_CONFIG.items()):
        raise DemoError("Stable optimized app settings differ from the fixed contract")
    write_json(EVIDENCE / "app-config.json", config)
    http_json(context.staging + "/admin/reset", payload={}, headers=headers)
    summary = read_json(EVIDENCE / "summary.json")
    for _ in range(3):
        summary["functional_sha256"] = functional_digest(http_json(
            context.staging + "/api/accounts/ACC-1001/transactions?page_size=40",
        ))
    check_mr(context)
    if context.main_sha() != summary["base_main_sha"]:
        raise DemoError("main drifted during candidate preparation")
    write_json(EVIDENCE / "summary.json", summary)
    print("Exact SHA/pipeline, fixed app settings, authenticated reset and three 40-row warm-ups verified.")


def monitoring_input():
    metrics = {}
    for label in ("staging", "shared_plan"):
        metrics.update(selected_metrics(
            read_json(EVIDENCE / f"metric-definitions-{label}.json"),
            read_json(EVIDENCE / f"resource-{label}.json"), label,
        ))
    write_json(EVIDENCE / "selected-server-metrics.json", {"metrics": metrics})


def monitoring_check(context: Context):
    components = read_json(EVIDENCE / "app-components.json")["components"]
    metrics = read_json(EVIDENCE / "server-metric-definitions.json")["metrics"]
    if not {context.staging_id.lower(), context.plan_id.lower()}.issubset(key.lower() for key in components):
        raise DemoError("Native CLI did not retain staging/shared-plan app components")
    expected = read_json(EVIDENCE / "selected-server-metrics.json")["metrics"]
    if not {key.lower() for key in expected}.issubset(key.lower() for key in metrics):
        raise DemoError("Native CLI did not retain all seven server metric definitions")
    print("Actual staging slot + shared S1 plan and seven metric definitions verified; definitions are NOT samples.")


def telemetry_summary():
    result = {"client_gate_independent": True, "resources": {}}
    for label in ("staging", "shared_plan"):
        files = list(EVIDENCE.glob(f"server-metrics-{label}-*.json"))
        count = sum(sample_count(read_json(path)) for path in files)
        expected = 2 if label == "staging" else 1
        result["resources"][label] = {
            "status": (
                "partial_samples" if count and len(files) < expected else
                "samples_available" if count else "pending_ingestion" if files else "unavailable"
            ),
            "sample_count": count, "available_files": len(files), "expected_files": expected,
        }
    logs = EVIDENCE / "staging-console-logs.json"
    count = len(read_json(logs)) if logs.exists() else 0
    result["logs"] = {
        "status": ("rows_available" if count else "pending_ingestion") if logs.exists() else "unavailable",
        "row_count": count,
    }
    write_json(EVIDENCE / "monitoring-summary.json", result)
    return result


def finish(context: Context, code: int) -> int:
    path = EVIDENCE / "summary.json"
    summary = read_json(path) if path.exists() else {"source_sha": context.source_sha, "job_id": context.job_id}
    try:
        payload = read_json(EVIDENCE / "run.json")
        verdict = evaluate(payload)
        if code != verdict.exit_code or (EVIDENCE / "download-failed").exists():
            raise DemoError("A native CLI step or required evidence download failed")
        if verdict.outcome not in {"passed", "performance_failed"}:
            raise DemoError("No settled, judged client measurement")
        check_demo_contract(verdict)
        wait_version(context.staging, context.source_sha, context.pipeline_id, seconds=60)
        check_mr(context)
        if context.main_sha() != summary["base_main_sha"]:
            raise DemoError("main drifted during the locked load interval")
        summary.update(verdict.as_dict(), alt_start=payload["startDateTime"], alt_end=payload["endDateTime"])
    except (DemoError, ValueError, OSError, KeyError) as error:
        summary.update(outcome="infrastructure_failed", reason=str(error))
    summary.update(monitoring=telemetry_summary(), finished_at=now())
    write_json(path, summary)
    print(json.dumps(summary, indent=2))
    return {"passed": 0, "performance_failed": 1}.get(summary["outcome"], 2)


def verified_green(context: Context, job_id: str, mr_iid: str) -> dict:
    job = context.client.get(f"/jobs/{int(job_id)}")
    if job["name"] != "performance_gate" or job["status"] != "success":
        raise DemoError("Promotion requires the successful locked performance_gate job")
    producing = context.client.get(f"/pipelines/{job['pipeline']['id']}")
    if producing["sha"] != context.source_sha or producing["status"] != "success" or producing["source"] != "merge_request_event":
        raise DemoError("Approved MR pipeline is not successful for this exact source")
    proof = json.loads(context.client.request(
        "GET", f"{context.client.project}/jobs/{int(job_id)}/artifacts/demo-evidence/summary.json", raw=True,
    ))
    if (
        proof.get("outcome") != "passed" or proof.get("source_sha") != context.source_sha
        or str(proof.get("job_id")) != str(job_id) or str(proof.get("pipeline_id")) != str(producing["id"])
        or str(proof.get("mr_iid")) != str(mr_iid) or proof.get("contract_sha256") != contract_digest()
    ):
        raise DemoError("Gate evidence does not match this promotion")
    mr = context.client.get(f"/merge_requests/{int(mr_iid)}")
    head = mr.get("head_pipeline") or {}
    if mr["state"] != "merged" or mr["sha"] != context.source_sha or head.get("id") != producing["id"] or head.get("status") != "success":
        raise DemoError("Merged MR does not point at the approved latest green pipeline")
    return proof


def promotion_prepare(context: Context):
    proof = verified_green(context, required("DEMO_GATE_JOB_ID"), required("DEMO_MR_IID"))
    expected = sha(required("DEMO_PROMOTE_SHA"))
    if context.source_sha != expected or required("CI_COMMIT_BRANCH") != "main" or context.main_sha() != expected:
        raise DemoError("Only the current fast-forwarded tested main commit may be promoted")
    if context.client.get("/merge_requests?state=opened&per_page=100"):
        raise DemoError("Another candidate is open")
    wait_version(context.staging, expected, str(proof["pipeline_id"]), seconds=60)
    write_json(EVIDENCE / "approved-gate.json", proof)
    export("promotion.env", {"ALT_RUN_ID": proof["alt_run_id"]})


def promotion_finish(context: Context):
    proof = read_json(EVIDENCE / "approved-gate.json")
    if context.main_sha() != context.source_sha:
        raise DemoError("main drifted during promotion")
    version = wait_version(context.production, context.source_sha, str(proof["pipeline_id"]))
    if context.main_sha() != context.source_sha:
        raise DemoError("main changed during the tested slot swap")
    write_json(EVIDENCE / "promotion.json", {
        "outcome": "promoted", "source_sha": context.source_sha, "pipeline_id": context.pipeline_id,
        "gate_pipeline_id": proof["pipeline_id"], "alt_run_id": proof["alt_run_id"],
        "artifact_sha256": proof["artifact_sha256"], "production_url": context.production,
        "version": version, "finished_at": now(),
    })


def sanitize(text: str) -> str:
    for key in ("GITLAB_BOT_TOKEN", "DEMO_ADMIN_TOKEN", "IDENTITY_HEADER", "MSI_SECRET"):
        value = os.getenv(key)
        if value:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"https?://[^\s\"<>]+", lambda match: match[0].split("?")[0] + ("?[REDACTED]" if "?" in match[0] else ""), text)
    return re.sub(r"(?im)^.*(?:authorization|private-token|x-demo-admin-token)\s*:.*$", "[REDACTED HEADER]", text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=[
        "build", "prepare", "warm", "finish", "monitoring-input", "monitoring-check",
        "window", "sanitize", "promotion-prepare", "promotion-finish",
    ])
    parser.add_argument("--sha")
    parser.add_argument("--pipeline-id")
    parser.add_argument("--exit-code", type=int, default=2)
    args = parser.parse_args()
    try:
        if args.operation == "sanitize":
            print("\n".join(sanitize(sys.stdin.read()).splitlines()[:40]))
        elif args.operation == "build":
            build(args.sha or required("CI_COMMIT_SHA"), args.pipeline_id or required("CI_PIPELINE_ID"))
        elif args.operation == "monitoring-input":
            monitoring_input()
        elif args.operation == "window":
            start, end = query_window(read_json(EVIDENCE / "run.json"))
            export("telemetry.env", {"METRIC_START": start, "METRIC_END": end})
        else:
            context = Context()
            if args.operation == "finish":
                return finish(context, args.exit_code)
            {
                "prepare": prepare, "warm": warm, "monitoring-check": monitoring_check,
                "promotion-prepare": promotion_prepare, "promotion-finish": promotion_finish,
            }[args.operation](context)
        return 0
    except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
