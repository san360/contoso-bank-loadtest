"""Pure metric-definition/sample bookkeeping; Azure calls live in .gitlab-ci.yml."""

from datetime import datetime, timedelta

from .common import DemoError

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
            raise DemoError(f"{label} does not expose the required definition {name}")
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


def sample_count(response: dict) -> int:
    return sum(
        1 for metric in response.get("value", [])
        for series in metric.get("timeseries", []) for point in series.get("data", [])
        if any(point.get(key) is not None for key in ("average", "total", "minimum", "maximum", "count"))
    )


def query_window(run: dict) -> tuple[str, str]:
    if not isinstance(run.get("startDateTime"), str) or not isinstance(run.get("endDateTime"), str):
        raise DemoError("Run has no completed measurement window for telemetry")
    start = datetime.fromisoformat(run["startDateTime"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(run["endDateTime"].replace("Z", "+00:00"))
    return (
        (start.replace(second=0, microsecond=0) - timedelta(minutes=1)).isoformat(),
        (end.replace(second=0, microsecond=0) + timedelta(minutes=2)).isoformat(),
    )
