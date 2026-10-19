"""Bootstrap only this isolated lab; Terraform is never invoked by MR pipelines."""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import secrets
import subprocess
import sys
import urllib.parse
from pathlib import Path

from .common import (
    APIError, DEMO_BRANCH, DemoError, GitLab, ROOT, read_json, run, write_json,
)


def private_directory(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(ROOT):
        raise DemoError("The private directory, credentials and Terraform state must be outside the repo")
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        identity = run(["whoami"])
        run([
            "icacls", str(path), "/inheritance:r", "/grant:r",
            f"{identity}:(OI)(CI)F", "SYSTEM:(OI)(CI)F", "/Q",
        ])
    else:
        path.chmod(0o700)
    return path


def initialize(args) -> None:
    folder = private_directory(args.private_dir)
    path = folder / "secrets.json"
    if path.exists():
        raise DemoError(f"Credentials already exist at {path}; reuse them, do not overwrite cloud state")
    credentials = {
        "gitlab_root_password": secrets.token_urlsafe(36),
        "gitlab_bootstrap_token": "glpat-" + secrets.token_hex(24),
        "app_admin_token": secrets.token_urlsafe(36),
    }
    variables = {
        "subscription_id": args.subscription,
        "tenant_id": args.tenant,
        "location": args.location,
        **credentials,
    }
    write_json(path, credentials)
    write_json(folder / "demo.tfvars.json", variables)
    print(f"Owner-only credentials: {path}")
    print(f"Terraform variable file: {folder / 'demo.tfvars.json'}")
    print(f"Use local backend state path: {folder / 'terraform.tfstate'}")


def put_variable(client: GitLab, key: str, value: str, *, secret=False) -> None:
    body = {
        "key": key, "value": value, "protected": True, "masked": secret,
        "raw": True, "environment_scope": "*",
    }
    try:
        client.request("GET", f"{client.project}/variables/{key}")
    except APIError as error:
        if error.status != 404:
            raise
        client.request("POST", f"{client.project}/variables", body)
    else:
        client.request("PUT", f"{client.project}/variables/{key}", body)


def seed(args) -> None:
    folder = private_directory(args.private_dir)
    credentials = read_json(folder / "secrets.json")
    resources = read_json(folder / "resources.json")
    root = GitLab(resources["gitlab_url"], credentials["gitlab_bootstrap_token"])
    config_path = folder / "gitlab.json"
    if config_path.exists():
        config = read_json(config_path)
        bot = GitLab(resources["gitlab_url"], credentials["project_token"], config["project_id"])
        bot.get("/repository/branches/main")
        print(f"Reusing seeded project {config['project_url']}; no tokens or projects created.")
        return
    head = run(["git", "rev-parse", "HEAD"])
    if run(["git", "status", "--porcelain"]):
        raise DemoError("Commit the implementation before seeding; the source must be reproducible")
    root.request("GET", "user")
    progress_path = folder / "bootstrap-project.json"
    if progress_path.exists():
        progress = read_json(progress_path)
        root.project_id = progress["project_id"]
        project = root.get()
    else:
        project = root.request("POST", "projects", {
            "name": "Contoso Bank performance gate",
            "path": "contoso-bank",
            "visibility": "private",
            "initialize_with_readme": False,
            "default_branch": "main",
            "only_allow_merge_if_pipeline_succeeds": True,
            "allow_merge_on_skipped_pipeline": False,
            "only_allow_merge_if_all_discussions_are_resolved": True,
            "merge_method": "ff",
            "squash_option": "never",
            "auto_devops_enabled": False,
            "shared_runners_enabled": False,
            "build_timeout": 2400,
            "ci_allow_fork_pipelines_to_run_in_parent_project": False,
            "protect_merge_request_pipelines": True,
            "ci_pipeline_variables_minimum_override_role": "maintainer",
        })
        root.project_id = project["id"]
        progress = {"project_id": project["id"], "project_url": project["web_url"]}
        write_json(progress_path, progress)
    auth = base64.b64encode(f"oauth2:{credentials['gitlab_bootstrap_token']}".encode()).decode()
    env = {
        **os.environ,
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": f"http.{resources['gitlab_url']}/.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {auth}",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "",
        "GIT_TERMINAL_PROMPT": "0",
    }
    run(["git", "push", project["http_url_to_repo"], f"{head}:refs/heads/main"], env=env, timeout=300)
    for branch in ("main", DEMO_BRANCH):
        try:
            root.request("GET", f"{root.project}/protected_branches/{urllib.parse.quote(branch, safe='')}")
        except APIError as error:
            if error.status != 404:
                raise
            root.request("POST", f"{root.project}/protected_branches", {
                "name": branch, "push_access_level": 40, "merge_access_level": 40,
                "allow_force_push": False,
            })
    configured = root.get()
    if not configured.get("only_allow_merge_if_pipeline_succeeds"):
        raise DemoError("GitLab did not enable pipelines-must-succeed")
    if not configured.get("protect_merge_request_pipelines"):
        raise DemoError("GitLab did not enable protected resources for same-project protected MR branches")
    expires = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    if "project_token" not in credentials:
        token = root.request("POST", f"{root.project}/access_tokens", {
            "name": "bounded-demo-controller", "scopes": ["api", "write_repository"],
            "access_level": 40, "expires_at": expires,
        })
        credentials["project_token"] = token["token"]
        write_json(folder / "secrets.json", credentials)
    bot = GitLab(resources["gitlab_url"], credentials["project_token"], project["id"])
    if "runner_id" not in progress:
        runner = root.request("POST", "user/runners", {
            "runner_type": "project_type", "project_id": project["id"],
            "description": "ACA-only demo shell runner", "tag_list": ["aca-demo"],
            "run_untagged": False, "locked": True, "access_level": "ref_protected",
            "maximum_timeout": 2700,
        })
        credentials["runner_token"] = runner["token"]
        write_json(folder / "secrets.json", credentials)
        progress["runner_id"] = runner["id"]
        write_json(progress_path, progress)
    variables = read_json(folder / "demo.tfvars.json")
    variables.update({
        "runner_token": credentials["runner_token"], "deploy_gitlab": True, "deploy_runner": True,
    })
    write_json(folder / "demo.tfvars.json", variables)
    mapping = {
        "AZURE_SUBSCRIPTION_ID": "subscription_id",
        "AZURE_RESOURCE_GROUP": "resource_group",
        "APP_NAME": "app_name",
        "STAGING_URL": "staging_url",
        "PRODUCTION_URL": "production_url",
        "LOAD_TEST_NAME": "load_test_name",
        "AZURE_CLIENT_ID": "runner_client_id",
    }
    for name, source in mapping.items():
        put_variable(bot, name, resources[source])
    put_variable(bot, "DEMO_ADMIN_TOKEN", credentials["app_admin_token"], secret=True)
    put_variable(bot, "GITLAB_BOT_TOKEN", credentials["project_token"], secret=True)
    lint = bot.request("POST", f"{bot.project}/ci/lint", {
        "content": (ROOT / ".gitlab-ci.yml").read_text(), "dry_run": False,
    })
    if not lint.get("valid"):
        raise DemoError(f"GitLab rejected .gitlab-ci.yml: {lint.get('errors')}")
    config = {
        **resources,
        "project_id": project["id"],
        "project_url": project["web_url"],
        "runner_id": progress["runner_id"],
        "seed_sha": head,
        "demo_branch": DEMO_BRANCH,
    }
    write_json(config_path, config)
    root.request("DELETE", "personal_access_tokens/self")
    print(json.dumps({
        "project_url": project["web_url"], "project_id": project["id"],
        "runner_id": progress["runner_id"], "seed_sha": head,
        "next": "Apply the saved Terraform variables to start the project runner.",
        "credentials": str(folder / "secrets.json"),
        "bootstrap_token": "revoked",
    }, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    init = sub.add_parser("init")
    init.add_argument("--private-dir", required=True, type=Path)
    init.add_argument("--subscription", required=True)
    init.add_argument("--tenant", required=True)
    init.add_argument("--location", default="swedencentral")
    seed_parser = sub.add_parser("seed")
    seed_parser.add_argument("--private-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        {"init": initialize, "seed": seed}[args.operation](args)
        return 0
    except (DemoError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
