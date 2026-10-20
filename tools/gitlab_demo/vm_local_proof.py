"""Run inside the private VM: read its private GitLab binding, never emit tokens."""

import argparse
import hashlib
import ipaddress
import json
import subprocess
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--backend-url", default="http://10.74.4.4")
    args = parser.parse_args()
    backend = urllib.parse.urlsplit(args.backend_url)
    if (
        backend.scheme != "http" or not ipaddress.ip_address(backend.hostname).is_private
        or backend.username or backend.password or backend.path not in {"", "/"}
        or backend.query or backend.fragment
    ):
        raise RuntimeError("Backend proof requires this VM's private HTTP origin")
    result = subprocess.run(
        [
            "docker", "exec", "gitlab", "gitlab-rails", "runner",
            'STDOUT.write(Project.find(1).variables.find_by!(key: "GITLAB_BOT_TOKEN").value)',
        ],
        capture_output=True, text=True, timeout=240,
    )
    if result.returncode:
        raise RuntimeError("Restored CI token could not be decrypted; private Rails diagnostics were not emitted")
    token = result.stdout.strip()
    if not token or "\n" in token:
        raise RuntimeError("Restored token reader returned an invalid shape")
    host = urllib.parse.urlsplit(args.public_url).netloc

    def get(path, raw=False):
        request = urllib.request.Request(
            args.backend_url.rstrip("/") + "/api/v4/" + path,
            headers={"Host": host, "PRIVATE-TOKEN": token},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            value = response.read()
        return value if raw else json.loads(value)

    project = "projects/1"
    main_sha = get(project + "/repository/branches/main")["commit"]["id"]
    mr = get(project + "/merge_requests/1")
    pipelines = {str(item["id"]): item["status"] for item in get(project + "/pipelines?per_page=100")}
    artifact = get(project + "/jobs/9/artifacts/dist/candidate.zip", raw=True)
    digest = hashlib.sha256(artifact).hexdigest()
    if main_sha != args.source_sha or mr["state"] != "merged" or mr["sha"] != main_sha:
        raise RuntimeError("Restored main/MR does not match the completed tested scenario")
    if pipelines != {"1": "failed", "2": "failed", "3": "success", "4": "success"}:
        raise RuntimeError("Restored pipeline history differs from the completed scenario")
    if digest != args.artifact_sha256:
        raise RuntimeError("Restored tested ZIP checksum differs")
    for name in ("GITLAB_BOT_TOKEN", "DEMO_ADMIN_TOKEN"):
        variable = get(project + "/variables/" + name)
        if not variable["protected"] or not variable["value"]:
            raise RuntimeError("Restored protected CI data is missing or not decryptable")
        if name == "GITLAB_BOT_TOKEN" and variable["value"] != token:
            raise RuntimeError("Restored API token and protected variable disagree")
    print("DEMO_VM_PROOF=" + json.dumps({
        "main_sha": main_sha, "mr_iid": 1, "mr_state": mr["state"],
        "pipeline_states": pipelines, "artifact_sha256": digest,
        "protected_variables_decrypt": True, "version": get("version")["version"],
        "runner_id": 1, "runner_status": get("runners/1")["status"],
    }))


if __name__ == "__main__":
    main()
