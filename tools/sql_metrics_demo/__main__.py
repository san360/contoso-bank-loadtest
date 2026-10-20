"""Prepare, provision, run once, inspect and remove an isolated SQL metrics demo.

Python 3.11+, Azure CLI and Terraform 1.9+ are required.
Only prepare is cloud-read-only. Provision/run require --accept-cost. No command
changes az account, registers providers, starts old tests, or deploys the bank API.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from tools.gitlab_demo.azure_http import AzureAPIError, scoped_token
from tools.gitlab_demo.common import DemoError, azure, http_json, read_json, run, write_json
from tools.loadtest_gate import evaluate, run_snapshot
from tools.vuh_calculator import DEFAULT_PRICE_LOW, billed_vuh_for_run

from .client import LoadTest, trusted_artifact_url
from .contract import (
    ALT_ID, INFRA, JDBC_NAME, JDBC_SHA256, JDBC_URL, LOAD, METRICS, NAMESPACE,
    Context, check_plan, metric_configuration_patch, selected_metrics, state_directory, test_configuration,
)
from .evidence import TelemetryPending, database_evidence, measurement_window, verify, write_csv

TERMINAL = {"DONE", "FAILED", "VALIDATION_FAILURE", "CANCELLED", "CANCELED", "STOPPED"}
RUN_EVIDENCE = (
    "run.json", "run-manifest.json", "database-check.json", "sql-metrics.json", "sql-metrics.csv",
    "run-components.json", "run-server-metrics-config.json", "verified-evidence.json",
    "bootstrap-credential-cleanup.json", "failed-run-metric-evidence.json",
    "diagnostic-triage.json", "diagnostic-log-excerpts.json", "diagnostic-request-outcomes.json",
)


def az_json(context: Context, arguments: list[str], *, timeout: int = 180):
    return json.loads(azure([*arguments, "-o", "json"], context.subscription_id, timeout=timeout))


def token_claims(token: str) -> dict:
    try:
        encoded = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (IndexError, KeyError, ValueError, TypeError):
        raise DemoError("Azure CLI returned an unusable SQL access token") from None


def identity_oid(token: str) -> str:
    try:
        return str(UUID(token_claims(token)["oid"]))
    except (KeyError, ValueError, TypeError):
        raise DemoError("Azure CLI returned a SQL token without a usable operator object ID") from None


def managed_identity_client_id(subscription: str, tenant: str, principal: str) -> str:
    token = scoped_token(subscription, tenant, "https://graph.microsoft.com")
    try:
        result = http_json(
            f"https://graph.microsoft.com/v1.0/servicePrincipals/{principal}?$select=id,appId,servicePrincipalType",
            headers={"Authorization": "Bearer " + token},
        )
    except urllib.error.HTTPError as error:
        raise DemoError(f"Cannot read the known ALT identity's client ID (Graph HTTP {error.code}); no directory permissions were changed") from None
    if result.get("id", "").lower() != principal.lower() or result.get("servicePrincipalType") != "ManagedIdentity":
        raise DemoError("Directory lookup did not return the selected ALT managed identity")
    return str(UUID(result["appId"]))


def bootstrap_secret(context: Context) -> tuple[str, str]:
    token = scoped_token(context.subscription_id, context.tenant_id, "https://database.windows.net/")
    if identity_oid(token) != context.entra_admin_object_id:
        raise DemoError("The bootstrap operator differs from the new server's Entra administrator")
    expires = datetime.fromtimestamp(int(token_claims(token)["exp"]), timezone.utc)
    if expires < datetime.now(timezone.utc) + timedelta(minutes=15):
        raise DemoError("SQL bootstrap token expires too soon; refresh CLI authentication before starting a paid run")
    return token, expires.isoformat()


@contextmanager
def lock(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "operation.lock"
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError:
        raise DemoError(f"Another demo operation holds {path}; do not run concurrently") from None
    try:
        yield
    finally:
        path.unlink()


def current_alt(context: Context) -> dict:
    resource = az_json(context, ["resource", "show", "--ids", context.load_test_resource_id])
    identity = resource.get("identity") or {}
    if (resource.get("id", "").lower() != context.load_test_resource_id.lower()
            or resource.get("location", "").lower() != context.location
            or identity.get("principalId", "").lower() != context.load_test_principal_id.lower()
            or identity.get("tenantId", "").lower() != context.tenant_id.lower()
            or "SystemAssigned" not in identity.get("type", "")):
        raise DemoError("The selected ALT resource, region or system identity no longer matches prepare")
    return resource


def require_owned_resources(context: Context) -> None:
    for resource_id in (context.group_id, context.server_id, context.database_id):
        resource = az_json(context, ["resource", "show", "--ids", resource_id])
        if (resource.get("id", "").lower() != resource_id.lower()
                or resource.get("tags", {}).get("demo-id") != context.token
                or resource.get("tags", {}).get("demo") != "azure-sql-metrics"):
            raise DemoError(f"Refusing an unowned or changed demo resource: {resource_id}")


def driver(folder: Path) -> Path:
    path = folder / JDBC_NAME
    if not path.exists():
        with urllib.request.urlopen(JDBC_URL, timeout=60) as response:
            contents = response.read(5 * 1024 * 1024 + 1)
        if len(contents) > 5 * 1024 * 1024 or hashlib.sha256(contents).hexdigest() != JDBC_SHA256:
            raise DemoError("Microsoft JDBC download size/checksum mismatch; no artifact was accepted")
        path.write_bytes(contents)
    if hashlib.sha256(path.read_bytes()).hexdigest() != JDBC_SHA256:
        raise DemoError("Cached Microsoft JDBC artifact failed its pinned SHA-256 check")
    return path


def retail_meter(expression: str, unit: str) -> tuple[dict, str]:
    url = "https://prices.azure.com/api/retail/prices?$filter=" + urllib.parse.quote(expression)
    with urllib.request.urlopen(url, timeout=60) as response:
        prices = json.load(response)
    candidates = [
        item for item in prices["Items"]
        if item["unitOfMeasure"] == unit and item["currencyCode"] == "USD"
        and item.get("isPrimaryMeterRegion")
    ]
    if len(candidates) != 1:
        raise DemoError("No unambiguous retail meter; review selected-region/security pricing before provisioning")
    return candidates[0], url


def retail_cost(context: Context) -> dict:
    price, url = retail_meter(
        "serviceName eq 'SQL Database' and productName eq 'SQL Database Single Basic' "
        f"and skuName eq 'B' and armRegionName eq '{context.location}' and type eq 'Consumption'",
        "1/Day",
    )
    plan = az_json(context, ["security", "pricing", "show", "--name", "SqlServers"])
    tier = plan.get("pricingTier")
    if tier not in {"Free", "Standard"}:
        raise DemoError("Inherited SQL Defender pricing is unknown; confirm security charges before provisioning")
    defender_monthly, defender_source = 0.0, ""
    if tier == "Standard":
        defender, defender_source = retail_meter(
            "productName eq 'Microsoft Defender for SQL' and skuName eq 'Standard' "
            "and meterName eq 'Standard Node' and armRegionName eq 'Global' "
            "and type eq 'Consumption'",
            "1/Month",
        )
        defender_monthly = defender["retailPrice"]
    ongoing_monthly = price["retailPrice"] * 730 / 24 + 0.01 * 730 + 0.50 + defender_monthly
    _, billed_vuh, _ = billed_vuh_for_run(2, 1, 2)
    return {
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "region": context.location, "sql_sku": "Basic / 5 DTU / 2 GB / LRS",
        "sql_usd_per_day": price["retailPrice"],
        "sql_usd_per_730_hours": price["retailPrice"] * 730 / 24,
        "sql_usd_per_hour_equivalent": price["retailPrice"] / 24,
        "sql_meter_id": price["meterId"], "sql_price_source": url,
        "one_run_billed_vuh_estimate": billed_vuh,
        "one_run_alt_usd_estimate": billed_vuh * DEFAULT_PRICE_LOW,
        "alt_assumption": "USD 0.15/VUH; 10 users/engine and 10-minute billing floors. Existing ALT resource reused; verify agreement rates.",
        "private_endpoint_usd_per_hour_estimate": 0.01,
        "private_dns_usd_per_month_estimate": 0.50,
        "inherited_defender_pricing_tier": tier,
        "defender_usd_per_month_estimate": defender_monthly,
        "defender_price_source": defender_source,
        "ongoing_total_usd_per_day_estimate": ongoing_monthly * 24 / 730,
        "ongoing_total_usd_per_730_hours_estimate": ongoing_monthly,
        "network_assumption": "ALT creates a temporary public IP/load balancer/NSG during VNet injection. Allow about USD 0.03 per hour plus small traffic charges; no persistent NAT or application host.",
        "private_network_assumption": "SQL public access is disabled. One private endpoint (~USD 0.01/hour), one isolated private DNS zone (~USD 0.50/month) and small data/DNS-query charges continue until cleanup.",
        "security_assumption": "Inherited Standard SQL Defender is budgeted at its per-server monthly retail meter, even during a temporary trial. The helper never disables or changes protection.",
        "warning": "Basic does not auto-pause. SQL, private endpoint, DNS and applicable inherited Defender charges continue until cleanup; estimates exclude tax, contract discounts and existing infrastructure.",
    }


def prepare(args, folder: Path) -> dict:
    if (folder / "context.json").exists():
        raise DemoError("This instance already has context; use its existing commands or a new state folder")
    if not all((args.subscription, args.tenant, args.load_test_resource_id, args.name)):
        raise DemoError("prepare requires --subscription, --tenant, --load-test-resource-id and --name")
    subscription, tenant = str(UUID(args.subscription)), str(UUID(args.tenant))
    match = ALT_ID.fullmatch(args.load_test_resource_id)
    if not match or match[1].lower() != subscription:
        raise DemoError("ALT ARM ID must belong to --subscription")
    resource = json.loads(azure([
        "resource", "show", "--ids", args.load_test_resource_id, "-o", "json",
    ], subscription))
    identity = resource.get("identity") or {}
    if "SystemAssigned" not in identity.get("type", "") or identity.get("tenantId", "").lower() != tenant:
        raise DemoError("The existing ALT resource must already have a system identity in --tenant")
    operator = identity_oid(scoped_token(subscription, tenant, "https://database.windows.net/"))
    context = Context(
        subscription, tenant, args.name, resource["location"].lower(), resource["id"],
        operator, identity["principalId"].lower(),
        managed_identity_client_id(subscription, tenant, identity["principalId"]),
    )
    current_alt(context)
    editions = az_json(context, ["sql", "db", "list-editions", "--location", context.location, "--edition", "Basic"])
    if not any(
        objective.get("name") == "Basic"
        and objective.get("status") in {"Available", "Default"}
        and objective.get("performanceLevel") == {"unit": "DTU", "value": 5.0}
        for edition in editions for objective in edition.get("supportedServiceLevelObjectives", [])
    ):
        raise DemoError("Basic 5 DTU is unavailable in the selected ALT region; do not silently upgrade")
    for provider in ("Microsoft.Sql", "Microsoft.Network", "Microsoft.Batch", "Microsoft.LoadTestService"):
        status = az_json(context, ["provider", "show", "--namespace", provider])
        if status.get("registrationState") != "Registered":
            raise DemoError(f"{provider} is not registered; an authorized operator must review registration")
    costs = retail_cost(context)
    write_json(folder / "context.json", asdict(context))
    write_json(folder / "terraform.tfvars.json", context.tfvars())
    write_json(folder / "cost-estimate.json", costs)
    return {"prepared": True, "resource_group": context.group_name, "test_id": context.test_id, "costs": costs}


def terraform(context: Context, folder: Path, executable: str, arguments: list[str], *, timeout=900) -> str:
    if read_json(folder / "terraform.tfvars.json") != context.tfvars():
        raise DemoError("Terraform variables no longer match the explicitly prepared demo context")
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("TF_CLI_ARGS")},
        "TF_DATA_DIR": str(folder / "terraform-data"), "TF_WORKSPACE": "default",
        "ARM_SUBSCRIPTION_ID": context.subscription_id, "ARM_TENANT_ID": context.tenant_id,
        "TF_IN_AUTOMATION": "1",
    }
    return run([executable, "-chdir=" + str(INFRA), *arguments], timeout=timeout, env=environment)


def provision(context: Context, folder: Path, args) -> dict:
    if not args.accept_cost:
        raise DemoError("Provisioning starts ongoing SQL charges; explicitly pass --accept-cost")
    current_alt(context)
    if not (folder / "terraform.tfstate").exists():
        exists = azure(["group", "exists", "--name", context.group_name, "-o", "tsv"], context.subscription_id)
        if exists.strip().lower() != "false":
            raise DemoError("The deterministic demo resource group already exists without this state; refusing adoption")
    costs = retail_cost(context)
    write_json(folder / "cost-estimate.json", costs)
    terraform(context, folder, args.terraform, [
        "init", "-input=false", "-no-color", "-backend-config=path=" + str(folder / "terraform.tfstate"),
    ])
    terraform(context, folder, args.terraform, ["validate", "-no-color"])
    plan = folder / "provision.tfplan"
    terraform(context, folder, args.terraform, [
        "plan", "-input=false", "-no-color", "-var-file=" + str(folder / "terraform.tfvars.json"), "-out=" + str(plan),
    ])
    check_plan(json.loads(terraform(context, folder, args.terraform, ["show", "-json", str(plan)])), context)
    terraform(context, folder, args.terraform, ["apply", "-input=false", "-no-color", str(plan)])
    resources = json.loads(terraform(context, folder, args.terraform, ["output", "-json", "resources"]))
    expected = {
        "resource_group_id": context.group_id, "server_id": context.server_id,
        "database_id": context.database_id, "subnet_id": context.subnet_id,
        "server_fqdn": context.host, "load_test_resource_id": context.load_test_resource_id,
        "load_test_principal_id": context.load_test_principal_id,
    }
    if any(str(resources.get(key, "")).lower() != value.lower() for key, value in expected.items()):
        raise DemoError("Terraform outputs do not match the isolated demo contract")
    write_json(folder / "resources.json", resources)
    require_owned_resources(context)
    return {
        "provisioned": True, "costs": costs,
        "portal_url": "https://portal.azure.com/#resource" + context.database_id,
        "next": "setup uploads only the new test; run is a separate explicit action",
    }


def assert_own_test(client: LoadTest, context: Context, *, may_create=False):
    test = client.optional("tests/" + context.test_id)
    if test is None:
        if not may_create:
            raise DemoError("The new demo test has not been set up")
    elif test.get("description") != context.marker:
        raise DemoError("A test already uses this generated ID without the ownership marker; refusing to change it")
    return test


def configure_test(client: LoadTest, context: Context, folder: Path) -> None:
    assert_own_test(client, context, may_create=True)
    payload = test_configuration(context)
    client.request("PATCH", "tests/" + context.test_id, payload)
    files = [
        (INFRA / "schema.sql", "ADDITIONAL_ARTIFACTS"),
        (LOAD / "bootstrap.groovy", "ADDITIONAL_ARTIFACTS"),
        (LOAD / "identity.groovy", "ADDITIONAL_ARTIFACTS"),
        (LOAD / "transaction.groovy", "ADDITIONAL_ARTIFACTS"),
        (driver(folder), "ADDITIONAL_ARTIFACTS"),
        (LOAD / "user.properties", "USER_PROPERTIES"),
        (LOAD / "sql-metrics.jmx", "JMX_FILE"),
    ]
    for path, kind in files:
        route = f"tests/{context.test_id}/files/{path.name}"
        client.request("PUT", route + "?fileType=" + kind, data=path.read_bytes())
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = client.request("GET", route).get("validationStatus")
            if status in {"VALIDATION_SUCCESS", "VALIDATION_NOT_REQUIRED"}:
                break
            if status == "VALIDATION_FAILURE":
                raise DemoError(f"Azure rejected the demo artifact {path.name}")
            time.sleep(3)
        else:
            raise DemoError(f"Azure did not validate {path.name} within 180 seconds")
    definitions = az_json(context, ["monitor", "metrics", "list-definitions", "--resource", context.database_id])
    metrics = selected_metrics(definitions, context)
    component = {
        "resourceType": NAMESPACE, "resourceName": "metrics-demo",
        "displayName": "Dedicated Basic SQL database",
    }
    client.request("PATCH", f"tests/{context.test_id}/app-components", {"components": {context.database_id: component}})
    metric_route = f"tests/{context.test_id}/server-metrics-config"
    existing = client.request("GET", metric_route)
    client.request("PATCH", metric_route, metric_configuration_patch(existing, metrics, context))
    write_json(folder / "metric-definitions.json", definitions)
    write_json(folder / "test-configuration.json", payload)


def setup(context: Context, folder: Path, args) -> dict:
    if not (folder / "resources.json").exists():
        raise DemoError("Provision this isolated instance before setup")
    current_alt(context)
    require_owned_resources(context)
    client = LoadTest(context.subscription_id, context.tenant_id, context.load_test_resource_id)
    assert_own_test(client, context, may_create=True)
    driver(folder)
    configure_test(client, context, folder)
    write_json(folder / "setup.json", {"test_id": context.test_id, "configured": True})
    return {"ready": True, "test_id": context.test_id, "load_started": False}


def stop_owned_run(client: LoadTest, context: Context, run_id: str) -> None:
    snapshot = client.optional("test-runs/" + run_id)
    if snapshot is None:
        return
    if snapshot.get("testId") != context.test_id:
        raise DemoError("Refusing to stop a run belonging to another test")
    if snapshot.get("status") not in TERMINAL:
        client.request("POST", "test-runs/" + run_id + ":stop")


def wait_for_run(client: LoadTest, context: Context, folder: Path, run_id: str) -> dict:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        snapshot = client.get_run(run_id)
        if snapshot.get("testId") != context.test_id or snapshot.get("testRunId") != run_id:
            raise DemoError("Azure returned a different test/run")
        write_json(folder / "run.json", run_snapshot(snapshot))
        verdict = evaluate(snapshot)
        if snapshot.get("status") in TERMINAL and verdict.outcome != "pending":
            if verdict.outcome != "passed":
                raise DemoError(f"SQL run did not pass: {verdict.reason}; inspect run.json")
            return snapshot
        time.sleep(10)
    stop_owned_run(client, context, run_id)
    raise DemoError("Run/verdict did not settle in 15 minutes; stop requested for this demo run only")


def collect(context: Context, folder: Path, args, client: LoadTest, manifest: dict) -> dict:
    snapshot = wait_for_run(client, context, folder, manifest["run_id"])
    database_path = folder / "database-check.json"
    if not database_path.exists():
        for attempt in range(11):
            info = snapshot.get("testArtifacts", {}).get("outputArtifacts", {}).get("resultFileInfo") or {}
            if info.get("url"):
                with urllib.request.urlopen(trusted_artifact_url(info["url"]), timeout=60) as response:
                    content = response.read(10 * 1024 * 1024 + 1)
                database = database_evidence(content, manifest["run_token"])
                write_json(database_path, database)
                break
            if attempt == 10:
                raise DemoError("SQL verification results were unavailable after five minutes")
            time.sleep(30)
            snapshot = client.get_run(manifest["run_id"])
    database = read_json(database_path)
    components = client.request("GET", f"test-runs/{manifest['run_id']}/app-components")
    configuration = client.request("GET", f"test-runs/{manifest['run_id']}/server-metrics-config")
    if set(key.lower() for key in components.get("components", {})) != {context.database_id.lower()}:
        raise DemoError("Run did not snapshot exactly the new SQL database as its app component")
    configured = list(configuration.get("metrics", {}).values())
    if (len(configured) != len(METRICS)
            or {(item.get("name"), item.get("aggregation")) for item in configured} != set(METRICS.items())
            or any(item.get("resourceId", "").lower() != context.database_id.lower()
                   or item.get("metricNamespace", "").lower() != NAMESPACE.lower() for item in configured)):
        raise DemoError("Run did not snapshot the selected Basic SQL server metrics")
    write_json(folder / "run-components.json", components)
    write_json(folder / "run-server-metrics-config.json", configuration)
    start, end = measurement_window(snapshot)
    query_start = start.replace(second=0, microsecond=0)
    query_end = end.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for attempt in range(11):
        payloads = []
        for aggregation in ("Average", "Total"):
            names = [name for name, value in METRICS.items() if value == aggregation]
            payloads.append(az_json(context, [
                "monitor", "metrics", "list", "--resource", context.database_id,
                "--namespace", NAMESPACE, "--metrics", *names, "--aggregation", aggregation,
                "--interval", "PT1M", "--start-time", query_start.isoformat(), "--end-time", query_end.isoformat(),
            ]))
        write_json(folder / "sql-metrics.json", payloads)
        write_csv(folder / "sql-metrics.csv", payloads, snapshot, context, database=database)
        try:
            summary = verify(snapshot, database, payloads, context, manifest["run_token"])
        except TelemetryPending:
            if attempt == 10:
                raise
            time.sleep(30)
        else:
            write_json(folder / "verified-evidence.json", summary)
            return summary
    raise DemoError("SQL metric collection exhausted its bounded polling window")


def clear_bootstrap_secret(client: LoadTest, context: Context, folder: Path, run_id: str) -> dict:
    snapshot = client.optional("test-runs/" + run_id)
    if snapshot is None:
        return {"removed": False, "reason": "Run not yet visible; inspect after an ambiguous start"}
    if snapshot.get("testId") != context.test_id:
        raise DemoError("Refusing credential changes on a foreign run")
    if snapshot.get("status") not in TERMINAL:
        return {"removed": False, "reason": "Run is stopping; inspect after it reaches a terminal state"}
    try:
        client.request("PATCH", "test-runs/" + run_id, {"secrets": {"BOOTSTRAP_SQL_TOKEN": None}})
    except AzureAPIError as error:
        if error.status not in {400, 404, 405, 409, 422}:
            raise
        result = {"removed": False, "reason": f"Terminal-run bootstrap-secret removal was rejected (HTTP {error.status}); the Entra token expires at the manifest timestamp"}
    else:
        after = client.get_run(run_id)
        if (after.get("status") != snapshot.get("status")
                or after.get("startDateTime") != snapshot.get("startDateTime")):
            raise DemoError("Run state changed unexpectedly while removing its bootstrap secret")
        result = {
            "removed": "BOOTSTRAP_SQL_TOKEN" not in (after.get("secrets") or {}),
            "reason": "Terminal-run merge-patch accepted; checked run metadata without saving secret fields",
        }
    write_json(folder / "bootstrap-credential-cleanup.json", result)
    return result


def execute_run(context: Context, folder: Path, args) -> dict:
    if not args.accept_cost:
        raise DemoError("run requires --accept-cost; it spends VUH and generates real SQL work")
    if not (folder / "setup.json").exists():
        raise DemoError("Complete setup before starting the one permitted demo run")
    manifest_path = folder / "run-manifest.json"
    previous = read_json(manifest_path) if manifest_path.exists() else None
    retry = getattr(args, "retry_failed_run", None)
    if previous and not retry:
        raise DemoError("This instance already requested its one run. Use inspect; never retry a possibly started run")
    if retry and (not previous or retry != previous.get("run_id") or (folder / "validation-retry.json").exists()):
        raise DemoError("Only one explicitly authorized retry of this exact recorded failed run is permitted")
    current_alt(context)
    require_owned_resources(context)
    client = LoadTest(context.subscription_id, context.tenant_id, context.load_test_resource_id)
    assert_own_test(client, context)
    if previous:
        failed = client.get_run(previous["run_id"])
        if (failed.get("testId") != context.test_id or failed.get("testRunId") != previous["run_id"]
                or evaluate(failed).outcome != "performance_failed"):
            raise DemoError("A retry requires settled FAILED results for this exact demo; never repeat an ambiguous start")
    configure_test(client, context, folder)
    run_token = str(uuid4())
    secret, expires = bootstrap_secret(context)
    if previous:
        if not previous["run_id"].startswith("sql-metrics-") or len(previous["run_id"]) != 44:
            raise DemoError("Unexpected previous run ID; refusing an archive path")
        UUID(hex=previous["run_id"].removeprefix("sql-metrics-"))
        archive = folder / "attempts" / previous["run_id"]
        archive.mkdir(parents=True, exist_ok=False)
        write_json(folder / "validation-retry.json", {"previous_run_id": previous["run_id"], "additional_starts_limit": 1})
        for name in RUN_EVIDENCE:
            path = folder / name
            if path.exists():
                path.replace(archive / name)
    manifest = {"run_id": "sql-metrics-" + uuid4().hex, "run_token": run_token, "bootstrap_token_expires_utc": expires}
    if previous:
        manifest["previous_failed_run_id"] = previous["run_id"]
    # Persist before the non-idempotent start: a timeout must never cause a second run.
    write_json(folder / "run-manifest.json", manifest)
    try:
        client.request("PATCH", "test-runs/" + manifest["run_id"], {
            "testId": context.test_id, "displayName": "Bounded SQL metrics proof",
            "environmentVariables": {
                "SQL_HOST": context.host, "SQL_DATABASE": "metrics-demo", "SQL_RUN_TOKEN": run_token,
                "SQL_ENGINE_SID": UUID(context.load_test_client_id).bytes_le.hex(),
            },
            "secrets": {"BOOTSTRAP_SQL_TOKEN": {"type": "SECRET_VALUE", "value": secret}},
            "debugLogsEnabled": False, "requestDataLevel": "NONE",
        })
        result = collect(context, folder, args, client, manifest)
    finally:
        stop_owned_run(client, context, manifest["run_id"])
        credential_cleanup = clear_bootstrap_secret(client, context, folder, manifest["run_id"])
    result["bootstrap_credential_cleanup"] = credential_cleanup
    write_json(folder / "verified-evidence.json", result)
    return result


def cleanup(context: Context, folder: Path, args) -> dict:
    if args.confirm_name != context.demo_name:
        raise DemoError("Cleanup requires --confirm-name matching this exact demo instance")
    current_alt(context)
    client = LoadTest(context.subscription_id, context.tenant_id, context.load_test_resource_id)
    manifest_path = folder / "run-manifest.json"
    if manifest_path.exists():
        run_id = read_json(manifest_path)["run_id"]
        stop_owned_run(client, context, run_id)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            snapshot = client.optional("test-runs/" + run_id)
            if snapshot is None or snapshot.get("status") in TERMINAL:
                break
            time.sleep(5)
        else:
            raise DemoError("Wait for this demo engine to stop before destroying its subnet")
    test = assert_own_test(client, context, may_create=True)
    if test is not None:
        client.request("DELETE", "tests/" + context.test_id)
    if (folder / "terraform.tfstate").exists():
        terraform(context, folder, args.terraform, [
            "init", "-input=false", "-no-color", "-backend-config=path=" + str(folder / "terraform.tfstate"),
        ])
        terraform(context, folder, args.terraform, ["validate", "-no-color"])
        plan = folder / "cleanup.tfplan"
        terraform(context, folder, args.terraform, [
            "plan", "-destroy", "-input=false", "-no-color",
            "-var-file=" + str(folder / "terraform.tfvars.json"), "-out=" + str(plan),
        ])
        check_plan(json.loads(terraform(context, folder, args.terraform, ["show", "-json", str(plan)])), context, destroy=True)
        terraform(context, folder, args.terraform, ["apply", "-input=false", "-no-color", str(plan)])
    exists = azure(["group", "exists", "--name", context.group_name, "-o", "tsv"], context.subscription_id)
    if exists.strip().lower() != "false":
        raise DemoError("Demo group still exists; cleanup is incomplete and SQL may still be billed")
    return {"removed": True, "resource_group": context.group_name, "test_id": context.test_id, "local_evidence_retained": str(folder)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("operation", choices=["prepare", "provision", "setup", "run", "inspect", "cleanup"])
    parser.add_argument("--state-dir", type=Path, required=True, help="Named ignored folder under .loadtest-demo/sql-metrics/")
    parser.add_argument("--subscription", help="Explicitly authorized subscription GUID (prepare only)")
    parser.add_argument("--tenant", help="Directory GUID for that subscription (prepare only)")
    parser.add_argument("--load-test-resource-id", help="Existing ALT ARM ID, never a name guessed from defaults (prepare only)")
    parser.add_argument("--name", help="New disposable instance name, 3-20 lowercase characters (prepare only)")
    parser.add_argument("--accept-cost", action="store_true", help="Authorize this provisioning action or exactly one bounded run")
    parser.add_argument("--retry-failed-run", help="Explicit extra-run authorization: exact recorded FAILED run ID; at most one additional start, preserving its evidence")
    parser.add_argument("--confirm-name", help="Exact instance name required for targeted cleanup")
    parser.add_argument("--terraform", default="terraform", help="Terraform executable or absolute path")
    args = parser.parse_args()
    try:
        folder = state_directory(args.state_dir)
        with lock(folder):
            if args.operation == "prepare":
                result = prepare(args, folder)
            else:
                context = Context(**read_json(folder / "context.json"))
                if args.operation == "inspect":
                    current_alt(context)
                    client = LoadTest(context.subscription_id, context.tenant_id, context.load_test_resource_id)
                    manifest = read_json(folder / "run-manifest.json")
                    try:
                        result = collect(context, folder, args, client, manifest)
                    finally:
                        credential_cleanup = clear_bootstrap_secret(client, context, folder, manifest["run_id"])
                    result["bootstrap_credential_cleanup"] = credential_cleanup
                    write_json(folder / "verified-evidence.json", result)
                else:
                    operation = {"provision": provision, "setup": setup, "run": execute_run, "cleanup": cleanup}[args.operation]
                    result = operation(context, folder, args)
        print(json.dumps(result, indent=2))
        return 0
    except (DemoError, OSError, ValueError, KeyError, subprocess.TimeoutExpired, urllib.error.URLError) as error:
        print(f"SQL metrics demo: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
