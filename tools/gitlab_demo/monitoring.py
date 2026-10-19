"""Candidate-slot and shared-plan visibility, separate from the client p95 gate."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .azure_http import ScopedLoadTest, scoped_token
from .common import TEST_ID, DemoError, azure, http_json, write_json

METRICS = {
    "staging": {
        "Requests": "Total", "Http5xx": "Total",
        "HttpResponseTime": "Average", "AverageMemoryWorkingSet": "Average",
    },
    "shared_plan": {
        "CpuPercentage": "Average", "MemoryPercentage": "Average", "HttpQueueLength": "Average",
    },
}


def selected_metrics(definitions: list[dict], resource: dict, label: str) -> dict:
    by_name = {item["name"]["value"]: item for item in definitions}
    selected = {}
    for name, aggregation in METRICS[label].items():
        if name not in by_name:
            raise DemoError(f"{label} does not expose the required metric definition {name}")
        definition = by_name[name]
        if aggregation not in definition.get("supportedAggregationTypes", []):
            raise DemoError(f"{label}/{name} does not support {aggregation}")
        selected[definition["id"]] = {
            "resourceId": resource["id"], "resourceType": resource["type"],
            "metricNamespace": definition["namespace"], "name": name,
            "aggregation": aggregation, "unit": definition["unit"],
            "displayDescription": (
                "Candidate staging slot only" if label == "staging"
                else "Shared S1 plan: includes staging and production, not candidate-isolated CPU"
            ),
        }
    return selected


def configure(context, folder: Path) -> dict:
    client = ScopedLoadTest(context.subscription, context.tenant, context.load_id)
    components = {}
    metrics = {}
    for label, resource_id in (("staging", context.staging_id), ("shared_plan", context.plan_id)):
        resource = json.loads(azure([
            "resource", "show", "--ids", resource_id, "-o", "json",
        ], context.subscription))
        definitions = json.loads(azure([
            "monitor", "metrics", "list-definitions", "--resource", resource_id, "-o", "json",
        ], context.subscription))
        write_json(folder / f"metric-definitions-{label}.json", definitions)
        components[resource_id] = {
            "resourceName": resource["name"], "resourceType": resource["type"],
            "kind": resource.get("kind", ""),
            "displayName": (
                "Candidate staging slot" if label == "staging" else "Shared S1 plan (not isolated CPU)"
            ),
        }
        metrics.update(selected_metrics(definitions, resource, label))
    client.request("PATCH", f"tests/{TEST_ID}", {"metricsReferenceIdentityType": "SystemAssigned"})
    client.request("PATCH", f"tests/{TEST_ID}/app-components", {"components": components})
    client.request("PATCH", f"tests/{TEST_ID}/server-metrics-config", {"metrics": metrics})
    actual_components = client.request("GET", f"tests/{TEST_ID}/app-components")
    actual_metrics = client.request("GET", f"tests/{TEST_ID}/server-metrics-config")
    if not set(key.lower() for key in components).issubset(
        key.lower() for key in actual_components["components"]
    ):
        raise DemoError("ALT did not retain the actual candidate slot and shared-plan components")
    if not set(key.lower() for key in metrics).issubset(key.lower() for key in actual_metrics["metrics"]):
        raise DemoError("ALT did not retain the requested server metric definitions")
    write_json(folder / "app-components.json", actual_components)
    write_json(folder / "server-metric-definitions.json", actual_metrics)
    result = {
        "status": "definitions_configured",
        "candidate_resource_id": context.staging_id,
        "shared_plan_resource_id": context.plan_id,
        "metric_count": len(metrics),
        "samples_collected": False,
        "note": "Definitions are not time-series samples; plan CPU is shared with production.",
    }
    write_json(folder / "monitoring-configuration.json", result)
    return result


def sample_count(response: dict[str, Any]) -> int:
    return sum(
        1 for metric in response.get("value", [])
        for series in metric.get("timeseries", [])
        for point in series.get("data", [])
        if any(point.get(aggregation) is not None for aggregation in ("average", "total", "minimum", "maximum", "count"))
    )


def query_window(run: dict) -> tuple[str, str]:
    if not isinstance(run.get("startDateTime"), str) or not isinstance(run.get("endDateTime"), str):
        raise DemoError("Run has no completed measurement window for server telemetry")
    start = datetime.fromisoformat(run["startDateTime"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(run["endDateTime"].replace("Z", "+00:00"))
    return (
        (start.replace(second=0, microsecond=0) - timedelta(minutes=1)).isoformat(),
        (end.replace(second=0, microsecond=0) + timedelta(minutes=2)).isoformat(),
    )


def collect(context, run: dict, folder: Path) -> dict:
    start, end = query_window(run)
    result = {
        "run_start": run["startDateTime"], "run_end": run["endDateTime"],
        "query_start": start, "query_end": end,
        "client_gate_independent": True,
        "note": "Minute buckets border the run; shared-plan CPU includes production.",
        "resources": {},
    }
    client = ScopedLoadTest(context.subscription, context.tenant, context.load_id)
    for route, name in (
        ("app-components", "run-app-components.json"),
        ("server-metrics-config", "run-server-metric-definitions.json"),
    ):
        try:
            write_json(folder / name, client.request("GET", f"test-runs/{run['testRunId']}/{route}"))
        except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
            result[name] = {"status": "error", "reason": str(error)}
            print(f"Run monitoring metadata unavailable ({route}): {error}", file=sys.stderr)
    for label, resource_id in (("staging", context.staging_id), ("shared_plan", context.plan_id)):
        responses = []
        try:
            for aggregation in sorted(set(METRICS[label].values())):
                names = [name for name, aggregate in METRICS[label].items() if aggregate == aggregation]
                response = json.loads(azure([
                    "monitor", "metrics", "list", "--resource", resource_id,
                    "--metric", *names, "--aggregation", aggregation, "--interval", "PT1M",
                    "--start-time", start, "--end-time", end, "-o", "json",
                ], context.subscription))
                responses.append(response)
            count = sum(sample_count(response) for response in responses)
            status = "samples_available" if count else "pending_ingestion"
            result["resources"][label] = {"status": status, "sample_count": count, "resource_id": resource_id}
            write_json(folder / f"server-metrics-{label}.json", {
                "resource_id": resource_id, "status": status, "sample_count": count,
                "query_start": start, "query_end": end, "responses": responses,
            })
        except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
            result["resources"][label] = {"status": "error", "reason": str(error), "resource_id": resource_id}
            print(f"Monitoring error ({label}): {error}", file=sys.stderr)
    try:
        client_metrics = json.loads(context.load_cli([
            "test-run", "metrics", "list", "--test-run-id", run["testRunId"],
            "--metric-namespace", "LoadTestRunMetrics", "--interval", "PT10S", "-o", "json",
        ]))
        write_json(folder / "client-metric-series.json", client_metrics)
        result["client_metric_series"] = {"status": "retrieved"}
    except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        result["client_metric_series"] = {"status": "error", "reason": str(error)}
        print(f"Client-series visibility error: {error}", file=sys.stderr)
    try:
        token = scoped_token(context.subscription, context.tenant, "https://api.loganalytics.io")
        query = (
            "AppServiceConsoleLogs "
            f"| where TimeGenerated between(datetime({start}) .. datetime({end})) "
            f"| where _ResourceId =~ '{context.staging_id}' "
            "| extend request=parse_json(ResultDescription) "
            f"| where tostring(request.source_sha) == '{context.source_sha}' "
            "| project TimeGenerated, request | take 100"
        )
        response = http_json(
            f"https://api.loganalytics.azure.com/v1/workspaces/{context.workspace}/query",
            payload={"query": query},
            headers={"Authorization": "Bearer " + token},
        )
        count = sum(len(table.get("rows", [])) for table in response.get("tables", []))
        status = "rows_available" if count else "pending_ingestion"
        result["logs"] = {"status": status, "row_count": count, "workspace": context.workspace}
        write_json(folder / "staging-console-logs.json", {"status": status, "row_count": count, "response": response})
    except urllib.error.HTTPError as error:
        result["logs"] = {"status": "unavailable", "http_status": error.code, "reason": "Logs query/ingestion not available"}
        print(f"Log Analytics visibility unavailable: HTTP {error.code}", file=sys.stderr)
    except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        result["logs"] = {"status": "error", "reason": str(error)}
        print(f"Log Analytics visibility error: {error}", file=sys.stderr)
    write_json(folder / "monitoring-summary.json", result)
    return result
