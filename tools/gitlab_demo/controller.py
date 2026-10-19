"""One external launch performs one explicitly scripted feature -> red -> batch fix -> green demo."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path

from tools.loadtest_gate import check_demo_contract, evaluate

from .common import (
    APIError, DEMO_BRANCH, FEATURE_FILE, DemoError, GitLab, ROOT, azure,
    contract_digest, http_json, read_json, sha, write_json,
)
from .pipeline import feature_name, now

TRAILER = "\n\nCo-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"


@contextmanager
def exclusive(path: Path):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise DemoError(f"Another launch or an unreconciled controller lock exists: {path}") from None
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def file_at(client: GitLab, path: str, ref: str) -> dict:
    return client.get(
        f"/repository/files/{urllib.parse.quote(path, safe='')}?ref={urllib.parse.quote(ref, safe='')}"
    )


def commit_recipe(client: GitLab, recipe: str, message: str, *, start_sha=None) -> str:
    branch = start_sha or DEMO_BRANCH
    current = file_at(client, FEATURE_FILE, branch)
    action = {
        "action": "update",
        "file_path": FEATURE_FILE,
        "content": (ROOT / "tools" / "gitlab_demo" / "recipes" / f"{recipe}.py").read_text(),
        "last_commit_id": current["last_commit_id"],
    }
    body = {
        "branch": DEMO_BRANCH,
        "commit_message": message + TRAILER,
        "actions": [action],
    }
    if start_sha:
        body["start_sha"] = start_sha
    result = client.request("POST", client.project + "/repository/commits", body)
    return sha(result["id"])


def wait_mr_pipeline(client: GitLab, iid: int, source_sha: str, *, seconds=2400) -> dict:
    deadline = time.monotonic() + seconds
    previous = None
    while time.monotonic() < deadline:
        mr = client.get(f"/merge_requests/{iid}")
        current_branch = client.get(
            f"/repository/branches/{urllib.parse.quote(DEMO_BRANCH, safe='')}"
        )["commit"]["id"]
        if current_branch != source_sha or mr["state"] != "opened":
            raise DemoError("MR source or state changed while the controller was waiting")
        pipeline = mr.get("head_pipeline")
        if pipeline and pipeline.get("sha") == source_sha:
            current = (pipeline["id"], pipeline["status"])
            if current != previous:
                print(f"MR !{iid}: pipeline {pipeline['id']} {pipeline['status']}", flush=True)
                previous = current
            if pipeline["status"] in {"success", "failed", "canceled", "skipped", "manual"}:
                full = client.get(f"/pipelines/{pipeline['id']}")
                if full["source"] != "merge_request_event":
                    raise DemoError("Expected an actual MR pipeline, not a branch/API pipeline")
                return full
        time.sleep(10)
    raise DemoError("MR pipeline did not finish within the bounded timeout; no automatic retry")


def gate_evidence(client: GitLab, pipeline: dict, *, expect_red: bool) -> tuple[dict, dict]:
    expected_status = "failed" if expect_red else "success"
    if pipeline["status"] != expected_status:
        raise DemoError(
            f"Expected a {'red' if expect_red else 'green'} pipeline; got {pipeline['status']}. "
            "No canned fix or retry will be applied."
        )
    jobs = client.get(f"/pipelines/{pipeline['id']}/jobs?per_page=100&include_retried=false")
    expected = {"functional_tests", "candidate_artifact", "performance_gate"}
    if {job["name"] for job in jobs} != expected or len(jobs) != 3:
        raise DemoError("The MR pipeline did not execute exactly the expected three jobs")
    by_name = {job["name"]: job for job in jobs}
    if any(by_name[name]["status"] != "success" for name in expected - {"performance_gate"}):
        raise DemoError("Functional tests or packaging failed; this is not the performance regression")
    job = by_name["performance_gate"]
    if job["status"] != expected_status:
        raise DemoError("Only the performance gate may fail during the scripted regression")
    if expect_red and job.get("failure_reason") != "script_failure":
        raise DemoError("The performance job did not fail with the expected gate exit")
    proof = json.loads(client.request(
        "GET", f"{client.project}/jobs/{job['id']}/artifacts/demo-evidence/summary.json", raw=True,
    ))
    if (
        proof.get("source_sha") != pipeline["sha"]
        or str(proof.get("pipeline_id")) != str(pipeline["id"])
        or str(proof.get("job_id")) != str(job["id"])
        or proof.get("contract_sha256") != contract_digest()
        or proof.get("outcome") != ("performance_failed" if expect_red else "passed")
    ):
        raise DemoError("Gate evidence does not establish the expected settled outcome for this source")
    return proof, job


def verify_measurement(config: dict, proof: dict, *, expect_red: bool) -> dict:
    payload = json.loads(azure([
        "load", "test-run", "show", "--test-run-id", proof["alt_run_id"],
        "--load-test-resource", config["load_test_name"],
        "--resource-group", config["resource_group"], "-o", "json",
    ], config["subscription_id"]))
    verdict = evaluate(payload)
    check_demo_contract(verdict)
    if expect_red:
        failed = [item for item in verdict.criteria if item["result"].lower() == "failed"]
        if (
            verdict.outcome != "performance_failed" or len(failed) != 1
            or failed[0]["aggregate"].lower() != "p95"
            or verdict.p95_ms <= 500 or verdict.error_pct > 1
        ):
            raise DemoError("Red was not a settled p95-only breach with acceptable errors; stopping")
    elif verdict.outcome != "passed" or verdict.p95_ms > 500 or verdict.error_pct > 1:
        raise DemoError("Fresh ALT evidence does not prove the unchanged criteria passed")
    return verdict.as_dict()


def note(client: GitLab, iid: int, body: str) -> None:
    client.request("POST", f"{client.project}/merge_requests/{iid}/notes", {"body": body})


def wait_merge_status(client: GitLab, iid: int, pipeline: dict, expected: str) -> dict:
    deadline = time.monotonic() + 120
    transient = {"checking", "unchecked", "ci_still_running", "ci_must_pass", "mergeable"}
    while time.monotonic() < deadline:
        mr = client.get(f"/merge_requests/{iid}?with_merge_status_recheck=true")
        branch = client.get(
            f"/repository/branches/{urllib.parse.quote(DEMO_BRANCH, safe='')}"
        )
        head = mr.get("head_pipeline") or {}
        if (
            mr["state"] != "opened" or branch["commit"]["id"] != pipeline["sha"]
            or head.get("id") != pipeline["id"] or head.get("status") != pipeline["status"]
        ):
            raise DemoError("The source or latest completed MR pipeline changed during merge checks")
        status = mr.get("detailed_merge_status")
        if status == expected and mr["sha"] == pipeline["sha"]:
            return mr
        if status not in transient:
            raise DemoError(f"Unexpected GitLab merge blocker: {status}")
        time.sleep(5)
    raise DemoError(f"GitLab merge checks did not settle to {expected} within 120 seconds")


def execute(folder: Path) -> dict:
    config = read_json(folder / "gitlab.json")
    credentials = read_json(folder / "secrets.json")
    client = GitLab(config["gitlab_url"], credentials["project_token"], config["project_id"])
    report = {"started_at": now(), "status": "starting", "scripted_not_autonomous_ai": True}
    report_path = folder / "demo-run.json"
    write_json(report_path, report)
    with exclusive(folder / "controller.lock"):
        try:
            project = client.get()
            if (
                not project.get("only_allow_merge_if_pipeline_succeeds")
                or project.get("allow_merge_on_skipped_pipeline")
                or project.get("merge_method") != "ff"
                or not project.get("protect_merge_request_pipelines")
            ):
                raise DemoError("Required merge checks, fast-forward mode or MR resource protection changed")
            if client.get("/merge_requests?state=opened&per_page=100"):
                raise DemoError("An MR is already open; this lab allows one candidate at a time")
            runners = client.get("/runners?per_page=100")
            if not any(
                item["id"] == config["runner_id"] and item.get("status") == "online"
                for item in runners
            ):
                raise DemoError("The project-specific ACA runner is not online")
            try:
                client.get(f"/repository/branches/{urllib.parse.quote(DEMO_BRANCH, safe='')}")
            except APIError as error:
                if error.status != 404:
                    raise
            else:
                raise DemoError("The fixed demo branch already exists. Use the documented explicit reset first")
            base = sha(client.get("/repository/branches/main")["commit"]["id"])
            original = base64.b64decode(file_at(client, FEATURE_FILE, base)["content"]).decode()
            if feature_name(original) != "none":
                raise DemoError("main already contains the feature. Reset this isolated demo before another launch")
            red_sha = commit_recipe(
                client, "naive", "Add merchant and category enrichment to transaction history",
                start_sha=base,
            )
            mr = client.request("POST", client.project + "/merge_requests", {
                "source_branch": DEMO_BRANCH, "target_branch": "main",
                "title": "Demo: merchant/category enrichment with a measured performance gate",
                "description": (
                    "This is an explicitly scripted demo, not autonomous AI remediation. "
                    "The first commit adds correct merchant/category output using per-row lookups. "
                    "Only a settled, measured p95 regression permits the controller to apply its "
                    "prewritten batching patch. Functional output and the load contract stay unchanged."
                ),
                "remove_source_branch": False, "squash": False,
            })
            iid = mr["iid"]
            report.update(mr_url=mr["web_url"], mr_iid=iid, base_main_sha=base, red_sha=red_sha)
            write_json(report_path, report)
            red_pipeline = wait_mr_pipeline(client, iid, red_sha)
            before, _ = gate_evidence(client, red_pipeline, expect_red=True)
            before["fresh_measurement"] = verify_measurement(config, before, expect_red=True)
            report.update(status="expected_regression_verified", before=before)
            write_json(report_path, report)
            wait_merge_status(client, iid, red_pipeline, "ci_must_pass")
            note(client, iid, (
                f"**Measured regression (scripted demo):** source `{red_sha}`, "
                f"pipeline [{red_pipeline['id']}]({red_pipeline['web_url']}), "
                f"ALT `{before['alt_run_id']}`: p95 **{before['p95_ms']} ms**, "
                f"errors **{before['error_pct']}%**. Functional tests passed; the unchanged "
                "p95 > 500 ms gate failed and GitLab blocked merge. Applying the prewritten "
                "batched lookup implementation, not changing settings, criteria or the feature."
            ))
            if client.get("/repository/branches/main")["commit"]["id"] != base:
                raise DemoError("main drifted before the scripted batching fix")
            green_sha = commit_recipe(
                client, "batched", "Batch merchant lookups while preserving enrichment output",
            )
            report["green_sha"] = green_sha
            write_json(report_path, report)
            green_pipeline = wait_mr_pipeline(client, iid, green_sha)
            after, green_job = gate_evidence(client, green_pipeline, expect_red=False)
            after["fresh_measurement"] = verify_measurement(config, after, expect_red=False)
            if (
                before["contract_sha256"] != after["contract_sha256"]
                or before["functional_sha256"] != after["functional_sha256"]
                or before["base_main_sha"] != base or after["base_main_sha"] != base
            ):
                raise DemoError("Feature output, load contract or target base changed between red and green")
            report.update(status="green_verified", after=after)
            write_json(report_path, report)
            note(client, iid, (
                f"**Batching fix verified:** source `{green_sha}`, "
                f"pipeline [{green_pipeline['id']}]({green_pipeline['web_url']}), "
                f"ALT `{after['alt_run_id']}`: p95 **{after['p95_ms']} ms**, "
                f"errors **{after['error_pct']}%**. Functional output and the complete load "
                "profile/criteria hashes match the red run. Fast-forward merge and "
                "promotion of this exact tested slot follow; no rebuild."
            ))
            mr = wait_merge_status(client, iid, green_pipeline, "mergeable")
            if (
                mr["sha"] != green_sha or (mr.get("head_pipeline") or {}).get("id") != green_pipeline["id"]
                or client.get("/repository/branches/main")["commit"]["id"] != base
            ):
                raise DemoError("The current source, latest pipeline or target base no longer matches green")
            merged = client.request("PUT", f"{client.project}/merge_requests/{iid}/merge", {
                "sha": green_sha, "should_remove_source_branch": False, "squash": False,
            })
            if merged["state"] != "merged" or client.get("/repository/branches/main")["commit"]["id"] != green_sha:
                raise DemoError("Merge did not fast-forward main to the tested source SHA")
            promotion = client.request("POST", client.project + "/pipeline", {
                "ref": "main",
                "variables": [
                    {"key": "DEMO_PROMOTE_SHA", "value": green_sha},
                    {"key": "DEMO_GATE_JOB_ID", "value": str(green_job["id"])},
                    {"key": "DEMO_MR_IID", "value": str(iid)},
                ],
            })
            report.update(status="promoting", promotion_pipeline_id=promotion["id"])
            write_json(report_path, report)
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                promotion = client.get(f"/pipelines/{promotion['id']}")
                if promotion["status"] in {"success", "failed", "canceled", "skipped", "manual"}:
                    break
                time.sleep(10)
            if promotion["status"] != "success" or promotion["sha"] != green_sha:
                raise DemoError(f"Promotion pipeline did not succeed: {promotion['status']}; no retry")
            live = http_json(config["production_url"] + "/version")
            if live.get("source_sha") != green_sha or str(live.get("pipeline_id")) != str(green_pipeline["id"]):
                raise DemoError("Production does not serve the exact artifact from the green gate")
            report.update(
                status="promoted", production_url=config["production_url"],
                production_version=live, promotion_pipeline_url=promotion["web_url"],
                finished_at=now(),
            )
            write_json(report_path, report)
            return report
        except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
            report.update(status="stopped", reason=str(error), finished_at=now())
            write_json(report_path, report)
            raise


def reset(folder: Path) -> None:
    config = read_json(folder / "gitlab.json")
    credentials = read_json(folder / "secrets.json")
    client = GitLab(config["gitlab_url"], credentials["project_token"], config["project_id"])
    with exclusive(folder / "controller.lock"):
        if client.get("/merge_requests?state=opened&per_page=100"):
            raise DemoError("Close or finish the open demo MR before an explicit reset")
        active = client.get("/pipelines?per_page=20")
        if any(item["status"] in {"running", "pending", "created", "preparing"} for item in active):
            raise DemoError("A pipeline is active; resetting would invalidate its evidence")
        baseline = (ROOT / FEATURE_FILE).read_text(encoding="utf-8")
        if feature_name(baseline) != "none":
            raise DemoError("Run reset from the implementation checkout, not the feature checkout")
        current = file_at(client, FEATURE_FILE, "main")
        if base64.b64decode(current["content"]).decode() != baseline:
            client.request("POST", client.project + "/repository/commits", {
                "branch": "main",
                "commit_message": "Reset isolated demo baseline for a new presentation" + TRAILER,
                "actions": [{
                    "action": "update", "file_path": FEATURE_FILE, "content": baseline,
                    "last_commit_id": current["last_commit_id"],
                }],
            })
        branch = urllib.parse.quote(DEMO_BRANCH, safe="")
        try:
            client.get(f"/repository/branches/{branch}")
        except APIError as error:
            if error.status != 404:
                raise
        else:
            client.request("DELETE", f"{client.project}/protected_branches/{branch}")
            try:
                client.request("DELETE", f"{client.project}/repository/branches/{branch}")
            finally:
                client.request("POST", f"{client.project}/protected_branches", {
                    "name": DEMO_BRANCH, "push_access_level": 40,
                    "merge_access_level": 40, "allow_force_push": False,
                })
        print("Isolated repository baseline reset. Production remains the previous tested artifact.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["run", "reset"])
    parser.add_argument("--private-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        folder = args.private_dir.resolve()
        if folder.is_relative_to(ROOT):
            raise DemoError("Controller credentials and state must be outside the checkout")
        if args.operation == "reset":
            reset(folder)
            return 0
        result = execute(folder)
        print(json.dumps(result, indent=2))
        return 0
    except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
