"""Fail-closed proof of real SQL work and run-contained Azure Monitor samples."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.gitlab_demo.common import DemoError
from tools.loadtest_gate import evaluate, number

from .contract import Context, METRICS, test_configuration


class TelemetryPending(DemoError):
    pass


def database_evidence(contents: bytes, run_token: str) -> dict:
    if len(contents) > 10 * 1024 * 1024:
        raise DemoError("Request-result archive exceeds the bounded evidence size")
    matches = []
    workload_samples = []
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            entries = archive.infolist()
            if len(entries) > 100 or sum(item.file_size for item in entries) > 20 * 1024 * 1024:
                raise DemoError("Request-result archive exceeds bounded expansion limits")
            for item in entries:
                if not item.filename.lower().endswith(".csv"):
                    continue
                text = archive.read(item).decode("utf-8-sig")
                for row in csv.DictReader(io.StringIO(text)):
                    if row.get("label") == "SQLTransaction" and row.get("success", "").lower() == "true":
                        start, elapsed = number(row.get("timeStamp")), number(row.get("elapsed"))
                        if start is None or elapsed is None or start < 0 or elapsed < 0:
                            raise DemoError("SQL request results have invalid workload timestamps")
                        workload_samples.append((start, start + elapsed))
                    message = row.get("responseMessage", "")
                    if (row.get("label") != "SQLVerify" or row.get("success", "").lower() != "true"
                            or not message.startswith("SQL_METRICS_EVIDENCE ")):
                        continue
                    value = json.loads(message.removeprefix("SQL_METRICS_EVIDENCE "))
                    if (not isinstance(value, dict) or set(value) != {
                            "run_token", "completed_operations", "account_count", "balance_delta",
                    } or not isinstance(value.get("run_token"), str)):
                        raise DemoError("SQL verification record has unexpected fields")
                    if value["run_token"].lower() == run_token.lower():
                        matches.append(value)
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeError, csv.Error):
        raise DemoError("Request results contain invalid archive or SQL verification data") from None
    if len(matches) != 1:
        raise DemoError("Expected exactly one non-secret SQL verification record for this run")
    if not workload_samples or len(workload_samples) > 240:
        raise DemoError("Expected bounded successful SQL request samples alongside database verification")
    result = matches[0]
    result.update({
        "successful_request_samples": len(workload_samples),
        "workload_start_utc": datetime.fromtimestamp(min(item[0] for item in workload_samples) / 1000, timezone.utc).isoformat(),
        "workload_end_utc": datetime.fromtimestamp(max(item[1] for item in workload_samples) / 1000, timezone.utc).isoformat(),
    })
    return result


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise DemoError("Metric/run timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def measurement_window(run: dict) -> tuple[datetime, datetime]:
    if not run.get("startDateTime") or not run.get("endDateTime"):
        raise DemoError("Run has no completed SQL measurement window")
    start, end = timestamp(run["startDateTime"]), timestamp(run["endDateTime"])
    if end <= start:
        raise DemoError("Run has an invalid measurement window")
    return start, end


def metric_rows(payloads: list[dict], run: dict, context: Context, *, workload_window=None) -> list[dict]:
    start, end = measurement_window(run)
    rows = []
    for payload in payloads:
        if payload.get("interval") != "PT1M":
            raise DemoError("SQL evidence requires one-minute Azure Monitor samples")
        for metric in payload.get("value", []):
            name = metric.get("name", {}).get("value")
            if name not in METRICS:
                continue
            expected_id = f"{context.database_id}/providers/Microsoft.Insights/metrics/{name}"
            if metric.get("id", "").lower() != expected_id.lower():
                raise DemoError("Azure Monitor returned a metric for a different database")
            aggregation = METRICS[name].lower()
            for series in metric.get("timeseries", []):
                for point in series.get("data", []):
                    value = number(point.get(aggregation))
                    if value is None:
                        continue
                    if value < 0 or (name.endswith("_percent") and value > 100):
                        raise DemoError(f"Azure Monitor returned an invalid SQL metric value for {name}")
                    sample_time = timestamp(point["timeStamp"])
                    overlap = 0.0 if workload_window is None else max(0.0, (
                        min(sample_time + timedelta(minutes=1), workload_window[1])
                        - max(sample_time, workload_window[0])
                    ).total_seconds())
                    rows.append({
                        "metric": name, "aggregation": METRICS[name],
                        "timestamp": sample_time.isoformat(), "value": value,
                        "inside_run": start <= sample_time and sample_time + timedelta(minutes=1) <= end,
                        "workload_overlap_seconds": overlap,
                    })
    return rows


def verify(run: dict, database: dict, payloads: list[dict], context: Context, run_token: str) -> dict:
    verdict = evaluate(run)
    if verdict.outcome != "passed":
        raise DemoError(f"SQL client results are not a measured pass: {verdict.reason}")
    if run.get("testId") != context.test_id:
        raise DemoError("Client evidence belongs to a different test")
    configuration = run.get("loadTestConfiguration", {})
    if configuration.get("engineInstances") != 1:
        raise DemoError("Client evidence is not from the single-engine demo")
    expected = test_configuration(context)["passFailCriteria"]["passFailMetrics"].values()
    fields = ("clientMetric", "aggregate", "condition", "value", "requestName")
    contract = {tuple(item.get(key) for key in fields) for item in expected}
    if {tuple(item.get(key) for key in fields) for item in verdict.criteria} != contract or len(verdict.criteria) != 3:
        raise DemoError("Run criteria differ from the fixed SQL demo contract")
    workload = run.get("testRunStatistics", {}).get("SQLTransaction", {})
    operations = number(workload.get("sampleCount"))
    p95 = number(workload.get("pct2ResTime"))
    if (operations is None or not 20 <= operations <= 240 or not operations.is_integer()
            or number(workload.get("errorCount")) != 0 or verdict.error_pct != 0
            or p95 is None or not 0 <= p95 <= 10000):
        raise DemoError("Expected 20-240 successful SQLTransaction samples")
    if (database.get("run_token", "").lower() != run_token.lower()
            or number(database.get("completed_operations")) != operations
            or number(database.get("successful_request_samples")) != operations
            or database.get("account_count") != 512
            or number(database.get("balance_delta")) != operations):
        raise DemoError("Database run token, committed work and client samples do not agree")
    workload_window = timestamp(database["workload_start_utc"]), timestamp(database["workload_end_utc"])
    start, end = measurement_window(run)
    if not start <= workload_window[0] < workload_window[1] <= end:
        raise DemoError("SQL request timestamps fall outside the selected run")
    rows = metric_rows(payloads, run, context, workload_window=workload_window)
    summary = {}
    for name in METRICS:
        selected = [
            row for row in rows if row["metric"] == name and row["inside_run"]
            and row["workload_overlap_seconds"] >= 30
        ]
        values = [row["value"] for row in selected]
        if not values:
            raise TelemetryPending(f"No numeric, fully run-contained SQL samples overlapping workload for {name}; telemetry is not verified")
        summary[name] = {
            "samples": len(values), "minimum": min(values), "maximum": max(values),
            "timestamps": [row["timestamp"] for row in selected],
        }
    if not any(summary[name]["maximum"] > 0 for name in ("cpu_percent", "dtu_consumption_percent", "log_write_percent")):
        raise TelemetryPending("SQL samples are all idle; no positive utilization evidence")
    if summary["sessions_count"]["maximum"] <= 0:
        raise TelemetryPending("SQL samples show no active sessions within the run")
    return {
        "verified": True, "test_id": context.test_id, "run_id": run["testRunId"],
        "database_id": context.database_id, "run_token": run_token,
        "committed_sql_operations": int(operations), "client_p95_ms": p95,
        "measurement_start": run["startDateTime"], "measurement_end": run["endDateTime"],
        "workload_start": database["workload_start_utc"], "workload_end": database["workload_end_utc"],
        "minimum_workload_overlap_seconds": 30,
        "metrics": summary,
        "attribution": "Dedicated database; matching SQL run token, committed-work count and successful request samples; complete one-minute run buckets overlapping at least 30 seconds of actual workload.",
    }


def write_csv(path: Path, payloads: list[dict], run: dict, context: Context, *, database=None) -> None:
    window = None if database is None else (timestamp(database["workload_start_utc"]), timestamp(database["workload_end_utc"]))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["metric", "aggregation", "timestamp", "value", "inside_run", "workload_overlap_seconds"])
        writer.writeheader()
        writer.writerows(metric_rows(payloads, run, context, workload_window=window))
