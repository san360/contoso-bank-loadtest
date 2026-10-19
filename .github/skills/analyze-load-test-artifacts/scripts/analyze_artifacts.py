"""Bounded, local-only triage of Azure Load Testing evidence. Never extracts ZIPs."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import io
import json
import math
import re
import zipfile
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 2000
MAX_SAMPLES = 1_000_000
LOG_PATTERNS = {
    "generator-memory": r"OutOfMemoryError|Java heap space|GC overhead limit|MemoryError",
    "dns": r"UnknownHostException|NameResolutionError|Name or service not known|nodename nor servname",
    "tls": r"SSLHandshakeException|CERTIFICATE_VERIFY_FAILED|certificate verify failed|PKIX path",
    "timeout": r"SocketTimeoutException|ConnectTimeout|ReadTimeout|connection timed out|Read timed out",
    "connection": r"Connection refused|ConnectionError|Connection reset|No route to host",
    "script-or-correlation": r"JSONDecodeError|KeyError:|ReferenceError|NOT_FOUND|Unknown User\(s\)",
    "engine-error": r"(?-i:\bERROR\b|\bFATAL\b)|Traceback \(most recent call last\)",
}


def redact(text: str) -> str:
    text = re.sub(r"(?i)(https?://[^\s?\"'<>]+)\?[^\s\"'<>]*", r"\1?[QUERY REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[JWT REDACTED]", text)
    text = re.sub(
        r"""(?i)(["']?(?:password|passwd|secret|token|authorization|api[-_]?key|"""
        r"""cookie|connection[-_]?string|AccountKey|SharedAccessSignature)["']?\s*[=:]\s*)"""
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", r"\1[REDACTED]", text)
    return text


def redact_object(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_object(item) for item in value]
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.search(
            r"(?i)(password|secret|token|authorization|api[-_]?key|cookie|connectionstring)", key)
            else redact_object(item) for key, item in value.items()}
    return value


def text_of(data: bytes) -> str:
    return data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")


def number(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Expected a finite, nonnegative number.")
    return result


def timestamp(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value) if re.fullmatch(r"\d+(?:\.\d+)?", str(value)) else (
            datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, OverflowError):
        return None


class Samples:
    def __init__(self) -> None:
        self.latencies: Counter[float] = Counter()
        self.codes: Counter[str] = Counter()
        self.count = 0
        self.failures = 0
        self.unknown = 0
        self.start: float | None = None
        self.end: float | None = None

    def add(self, elapsed: float, success: bool | None, code: str, stamp: float | None) -> None:
        self.count += 1
        self.latencies[elapsed] += 1
        self.codes[redact(code)[:120]] += 1
        self.failures += success is False
        self.unknown += success is None
        if stamp is not None:
            self.start = stamp if self.start is None else min(self.start, stamp)
            self.end = stamp + elapsed if self.end is None else max(self.end, stamp + elapsed)

    def summary(self) -> dict[str, Any]:
        ordered = sorted(self.latencies.items())

        def percentile(fraction: float) -> float | None:
            rank, seen = math.ceil(self.count * fraction), 0
            for value, count in ordered:
                seen += count
                if seen >= rank:
                    return value
            return None

        known = self.count - self.unknown
        duration = (self.end - self.start) / 1000 if self.start is not None and self.end is not None else None
        return {
            "requests": self.count, "failures": self.failures, "knownOutcomes": known,
            "unknownOutcomes": self.unknown,
            "errorRatePctOfKnownOutcomes": round(self.failures * 100 / known, 3) if known else None,
            "meanMs": round(sum(value * count for value, count in ordered) / self.count, 3) if self.count else None,
            "p50Ms": percentile(.5), "p95Ms": percentile(.95), "p99Ms": percentile(.99),
            "minMs": ordered[0][0] if ordered else None, "maxMs": ordered[-1][0] if ordered else None,
            "observedWindowSeconds": round(duration, 3) if duration is not None else None,
            "requestsPerObservedSecond": round(self.count / duration, 3) if duration and duration > 0 else None,
            "responseCodes": dict(self.codes.most_common(12)),
        }


class Analyzer:
    def __init__(self) -> None:
        self.report: dict[str, Any] = {
            "kind": "load-test-artifact-summary", "schemaVersion": 1,
            "runs": [], "requestFiles": [], "locustFailures": [], "engineLogFindings": [],
            "metricFiles": [], "files": [], "duplicates": [], "problems": [],
            "notes": [
                "Request files are reported separately; overlapping representations are not summed.",
                "Exact byte duplicates are counted once. Verify provenance for identical independent engine files.",
                "Percentiles are nearest-rank for raw samples; Locust stats retain their reported percentiles.",
                "Pattern redaction is not exhaustive. Review excerpts before sharing; never upload raw evidence.",
            ],
        }
        self.seen: dict[str, str] = {}
        self.total = 0
        self.file_count = 0

    def problem(self, source: str, message: str) -> None:
        self.report["problems"].append({"source": redact(source), "message": redact(message)})

    def analyze(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Evidence not found: {path}")
        if path.is_symlink():
            raise ValueError("Select the actual evidence file/directory, not a symlink.")
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for file in files:
            if file.is_symlink():
                self.problem(str(file), "Symlink skipped.")
                continue
            if not file.is_file():
                continue
            if file.suffix.lower() not in {".zip", ".gz", ".csv", ".jtl", ".log", ".out", ".txt", ".json"}:
                continue
            if file.stat().st_size > MAX_FILE:
                self.problem(str(file), "File exceeds the 64 MiB per-file limit.")
                continue
            if self.file_count >= MAX_FILES or self.total >= MAX_TOTAL:
                self.problem(str(file), "Inspection limit reached; remaining evidence was not read.")
                break
            try:
                self.visit(str(file.relative_to(path) if path.is_dir() else file.name), file.read_bytes())
            except OSError as exc:
                self.problem(str(file), f"Could not read evidence: {exc}")
        coverage = {
            "runMetadata": bool(self.report["runs"]),
            "requestResults": bool(self.report["requestFiles"]),
            "engineLogs": any(item.get("kind") == "engine-log" for item in self.report["files"]),
            "metricValues": bool(self.report["metricFiles"]),
        }
        self.report["coverage"] = coverage
        if not self.file_count:
            self.problem(str(path), "No supported evidence files found.")
        self.report["complete"] = not self.report["problems"]
        self.report["evidenceScope"] = "available files only; coverage is not proof of whole-run completeness"
        return redact_object(self.report)

    def visit(self, source: str, data: bytes, depth: int = 0) -> None:
        if len(data) > MAX_FILE or self.total + len(data) > MAX_TOTAL or self.file_count >= MAX_FILES:
            self.problem(source, "Decompressed/file inspection limit reached; content skipped.")
            return
        self.total += len(data)
        self.file_count += 1
        digest = hashlib.sha256(data).hexdigest()
        if digest in self.seen:
            self.report["duplicates"].append({"source": redact(source), "sameContentAs": redact(self.seen[digest])})
            return
        self.seen[digest] = source
        suffix = Path(source).suffix.lower()
        info: dict[str, Any] = {"source": redact(source), "bytes": len(data), "sha256": digest}
        self.report["files"].append(info)
        try:
            if suffix == ".zip":
                info["kind"] = "archive"
                if depth >= 2:
                    self.problem(source, "Archive nesting exceeds two levels.")
                    return
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for member in archive.infolist():
                        if member.is_dir():
                            continue
                        name = member.filename.replace("\\", "/")
                        parts = PurePosixPath(name)
                        if parts.is_absolute() or ".." in parts.parts or re.match(r"^[A-Za-z]:", name):
                            self.problem(f"{source}!{name}", "Unsafe archive member name skipped; no files were extracted.")
                            continue
                        if member.file_size > MAX_FILE:
                            self.problem(f"{source}!{name}", "Archive member exceeds 64 MiB limit.")
                            continue
                        if self.file_count >= MAX_FILES or self.total >= MAX_TOTAL:
                            self.problem(source, "Archive inspection limit reached.")
                            break
                        if Path(name).suffix.lower() not in {".zip", ".gz", ".csv", ".jtl", ".log", ".out", ".txt", ".json"}:
                            continue
                        with archive.open(member) as handle:
                            self.visit(f"{source}!{name}", handle.read(MAX_FILE + 1), depth + 1)
            elif suffix == ".gz":
                info["kind"] = "gzip"
                if depth >= 2:
                    self.problem(source, "Compressed nesting exceeds two levels.")
                    return
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
                    self.visit(source[:-3], handle.read(MAX_FILE + 1), depth + 1)
            else:
                text = text_of(data)
                if suffix == ".json":
                    info["kind"] = "json"
                    self.json_file(source, json.loads(text))
                elif suffix in {".csv", ".jtl"}:
                    info["kind"] = "request-or-metric-data"
                    if text.lstrip().startswith("<"):
                        self.xml_samples(source, text)
                    else:
                        self.csv_file(source, text)
                elif suffix in {".log", ".out", ".txt"}:
                    info["kind"] = "engine-log"
                    self.log_file(source, text)
        except (ValueError, OSError, EOFError, RuntimeError, NotImplementedError,
                zipfile.BadZipFile, ElementTree.ParseError, csv.Error, zlib.error) as exc:
            self.problem(source, f"Could not parse evidence: {exc}")

    def csv_file(self, source: str, text: str) -> None:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            self.problem(source, "Empty CSV: no header or samples.")
            return
        normalized = {re.sub(r"[\s_]", "", key).lower(): key for key in reader.fieldnames if key}
        if {"timestamp", "usercount"}.issubset(normalized):
            self.report["notes"].append(f"{redact(source)}: Locust history excluded from request totals.")
            return
        if {"requestcount", "failurecount"}.issubset(normalized):
            self.locust_stats(source, reader, normalized)
            return
        if {"error", "occurrences"}.issubset(normalized):
            for index, row in enumerate(reader, 2):
                if index > MAX_SAMPLES + 1:
                    self.problem(source, "Locust failure-row limit reached.")
                    break
                self.report["locustFailures"].append({
                    "source": redact(source), "line": index,
                    "name": redact(row.get(normalized.get("name", ""), ""))[:160],
                    "error": redact(row.get(normalized["error"], ""))[:300],
                    "occurrences": number(row[normalized["occurrences"]]),
                })
            return
        if {"metric", "value", "timestamp"}.issubset(normalized):
            self.metric_data(source, list(reader))
            return
        elapsed_key = normalized.get("elapsed") or normalized.get("responsetime")
        if not elapsed_key:
            self.problem(source, "Unsupported CSV columns: no raw elapsed time, Locust stats or metric schema.")
            return
        total = Samples()
        labels: dict[str, Samples] = defaultdict(Samples)
        invalid = 0
        for index, row in enumerate(reader, 2):
            if index > MAX_SAMPLES + 1:
                self.problem(source, "Sample limit reached; request statistics are partial.")
                break
            try:
                elapsed = number(row[elapsed_key])
            except (ValueError, TypeError):
                invalid += 1
                continue
            success_text = (row.get(normalized.get("success", ""), "") or "").lower()
            success = True if success_text in {"true", "1"} else False if success_text in {"false", "0"} else None
            label = redact(row.get(normalized.get("label", ""), "request") or "request")[:160]
            code = row.get(normalized.get("responsecode", ""), "") or ""
            stamp = timestamp(row.get(normalized.get("timestamp", ""), ""))
            total.add(elapsed, success, code, stamp)
            labels[label].add(elapsed, success, code, stamp)
        if invalid:
            self.problem(source, f"{invalid} malformed elapsed-time rows skipped; statistics are partial.")
        if not total.count:
            self.problem(source, "CSV contains no valid request samples.")
        self.report["requestFiles"].append({
            "source": redact(source), "kind": "raw-samples", "summary": total.summary(),
            "requests": [{"name": name, **samples.summary()} for name, samples in sorted(labels.items())],
        })

    def xml_samples(self, source: str, text: str) -> None:
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("DTD/entity declarations are not accepted.")
        root = ElementTree.fromstring(text)
        total = Samples()
        labels: dict[str, Samples] = defaultdict(Samples)
        nested = 0
        invalid = 0
        for element in root.iter():
            if element.tag not in {"sample", "httpSample"}:
                continue
            if any(child.tag in {"sample", "httpSample"} for child in element):
                nested += 1
                continue
            if total.count >= MAX_SAMPLES:
                self.problem(source, "XML sample limit reached; statistics are partial.")
                break
            try:
                elapsed = number(element.get("t"))
            except (ValueError, TypeError):
                invalid += 1
                continue
            outcome = element.get("s", "").lower()
            success = True if outcome == "true" else False if outcome == "false" else None
            label = redact(element.get("lb", "request"))[:160]
            code = element.get("rc", "")
            stamp = timestamp(element.get("ts"))
            total.add(elapsed, success, code, stamp)
            labels[label].add(elapsed, success, code, stamp)
        if nested:
            self.report["notes"].append(f"{redact(source)}: {nested} composite samples excluded; leaf samples only.")
        if invalid or not total.count:
            self.problem(source, f"XML has {invalid} malformed samples and {total.count} valid leaf samples.")
        self.report["requestFiles"].append({
            "source": redact(source), "kind": "raw-samples", "summary": total.summary(),
            "requests": [{"name": name, **samples.summary()} for name, samples in sorted(labels.items())],
        })

    def locust_stats(self, source: str, reader: csv.DictReader, columns: dict[str, str]) -> None:
        rows = []
        for index, row in enumerate(reader, 2):
            if index > MAX_SAMPLES + 1:
                self.problem(source, "Locust stats-row limit reached.")
                break
            count = number(row[columns["requestcount"]])
            failures = number(row[columns["failurecount"]])
            if not count.is_integer() or not failures.is_integer() or failures > count:
                raise ValueError(f"Invalid Locust request/failure counts on line {index}.")
            item: dict[str, Any] = {
                "name": redact(row.get(columns.get("name", ""), ""))[:160], "line": index,
                "requests": int(count), "failures": int(failures),
                "errorRatePctOfKnownOutcomes": round(100 * failures / count, 3) if count else None,
            }
            for column, key in (("averageresponsetime", "meanMs"), ("50%", "p50Ms"),
                                ("95%", "p95Ms"), ("99%", "p99Ms")):
                value = row.get(columns.get(column, ""), "")
                item[key] = number(value) if value else None
            rows.append(item)
        aggregate = next((row for row in rows if row["name"].lower() in {"aggregated", "aggregate"}), None)
        endpoints = [row for row in rows if row is not aggregate]
        if aggregate is None:
            count = sum(row["requests"] for row in endpoints)
            failures = sum(row["failures"] for row in endpoints)
            aggregate = {"requests": count, "failures": failures,
                         "errorRatePctOfKnownOutcomes": round(failures * 100 / count, 3) if count else None,
                         "p50Ms": None, "p95Ms": None, "p99Ms": None}
            self.report["notes"].append(f"{redact(source)}: no aggregate percentiles; endpoint percentiles were not averaged.")
        if not rows:
            self.problem(source, "Locust stats CSV contains no rows.")
        self.report["requestFiles"].append({
            "source": redact(source), "kind": "locust-final-stats",
            "summary": aggregate, "requests": endpoints,
        })

    def json_file(self, source: str, data: Any) -> None:
        if isinstance(data, dict) and data.get("kind") == "load-test-artifact-summary":
            return
        if isinstance(data, dict) and "status" in data and (
                "testRunId" in data or "passFailCriteria" in data):
            criteria = []
            groups = data.get("passFailCriteria") or {}
            for group in ("passFailMetrics", "passFailServerMetrics"):
                values = groups.get(group) or {}
                for key, item in (values.items() if isinstance(values, dict) else enumerate(values)):
                    if not isinstance(item, dict):
                        continue
                    criteria.append({"id": str(key), "kind": group, **{
                        name: item[name] for name in
                        ("clientMetric", "serverMetricName", "requestName", "aggregate",
                         "condition", "value", "actualValue", "result", "action") if name in item}})
            failures = sum(str(item.get("result", "")).lower() == "failed" for item in criteria)
            pending = sum(str(item.get("result", "")).lower() not in {"passed", "failed"} for item in criteria)
            status = str(data["status"]).upper()
            result = str(data.get("testResult", "UNKNOWN")).upper()
            conclusion = "no-verdict"
            if status in {"FAILED", "CANCELLED", "CANCELED", "STOPPED", "AUTOSTOPPED"}:
                conclusion = "execution-failed"
            elif failures or result == "FAILED":
                conclusion = "failed"
            elif result == "NOT_APPLICABLE" and not criteria:
                conclusion = "no-verdict"
            elif status == "DONE" and criteria and not pending and result == "PASSED":
                conclusion = "passed"
            elif status != "DONE" or criteria:
                conclusion = "unsettled"
            self.report["runs"].append({
                "source": redact(source), "testId": data.get("testId"), "testRunId": data.get("testRunId"),
                "status": status, "testResult": result, "performanceConclusion": conclusion,
                "startDateTime": data.get("startDateTime"), "endDateTime": data.get("endDateTime"),
                "criteria": criteria, "criterionFailures": failures, "criteriaPending": pending,
            })
        elif "metric" in source.lower() and "config" not in source.lower() and "definition" not in source.lower():
            self.metric_data(source, data)

    def metric_data(self, source: str, data: Any) -> None:
        values: dict[str, list[float]] = defaultdict(list)

        def visit(node: Any, name: str = "") -> None:
            if isinstance(node, list):
                for item in node:
                    visit(item, name)
            elif isinstance(node, dict):
                item_name = node.get("name")
                if isinstance(item_name, dict):
                    item_name = item_name.get("value")
                name = str(node.get("metric") or node.get("metricName") or item_name or name)
                if node.get("timestamp") or node.get("timeStamp"):
                    for key in ("value", "average", "total", "count", "minimum", "maximum"):
                        item = node.get(key)
                        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
                            values[name or "see export filename"].append(float(item))
                        elif isinstance(item, str):
                            try:
                                values[name or "see export filename"].append(number(item))
                            except ValueError:
                                self.problem(source, "Metric CSV contains a nonnumeric datapoint.")
                for key in ("value", "timeseries", "timeSeries", "data"):
                    if isinstance(node.get(key), (list, dict)):
                        visit(node[key], name)

        if (isinstance(data, dict)
                and not any(key in data for key in ("value", "timeseries", "timeSeries", "data"))):
            for name, series in data.items():
                visit(series, name)
        else:
            visit(data)
        for name, points in values.items():
            self.report["metricFiles"].append({
                "source": redact(source), "metric": redact(name), "datapoints": len(points),
                "minObservedValue": min(points), "maxObservedValue": max(points),
                "note": "Descriptive range only. Units/aggregation/dimensions remain in the original export.",
            })

    def log_file(self, source: str, text: str) -> None:
        findings: dict[str, dict[str, Any]] = {}
        for line_number, line in enumerate(text.splitlines(), 1):
            for category, pattern in LOG_PATTERNS.items():
                if not re.search(pattern, line, re.IGNORECASE):
                    continue
                finding = findings.setdefault(category, {
                    "source": redact(source), "category": category, "matchingLines": 0, "examples": []})
                finding["matchingLines"] += 1
                if len(finding["examples"]) < 3:
                    finding["examples"].append({"line": line_number, "excerpt": redact(line)[:300]})
        self.report["engineLogFindings"].extend(findings.values())


def markdown(report: dict[str, Any]) -> str:
    def safe(value: Any) -> str:
        return html.escape(redact(str(value))).replace("|", "\\|").replace("\n", " ")

    lines = ["# Load test evidence summary", "",
             f"**Parse coverage:** {'complete for supported input' if report['complete'] else 'PARTIAL - inspect problems below'}.",
             f"**Scope:** {report['evidenceScope']}.", "",
             "## Run outcome", "",
             "| Run | Execution | Service result | Evidence conclusion |",
             "| --- | --- | --- | --- |"]
    for run in report["runs"]:
        lines.append("| " + " | ".join(safe(run.get(key, "")) for key in
                                      ("testRunId", "status", "testResult", "performanceConclusion")) + " |")
    if not report["runs"]:
        lines.append("| Not provided | Unknown | Unknown | No performance verdict can be established |")
    lines += ["", "## Request evidence (files are not summed)", "",
              "| Source | Requests | Known failures | Unknown outcomes | Error % of known | p95 ms |",
              "| --- | --- | --- | --- | --- | --- |"]
    for item in report["requestFiles"]:
        summary = item["summary"]
        lines.append("| " + " | ".join(safe(value) for value in [
            item["source"], summary.get("requests"), summary.get("failures"),
            summary.get("unknownOutcomes", "not applicable"),
            summary.get("errorRatePctOfKnownOutcomes"), summary.get("p95Ms")]) + " |")
    lines += ["", "## Reported Locust failures", ""]
    for item in report["locustFailures"]:
        lines.append(f"- {safe(item['source'])}, line {item['line']}: "
                     f"{safe(item['name'])}: {safe(item['error'])} ({item['occurrences']} occurrences).")
    if not report["locustFailures"]:
        lines.append("No Locust failure records were provided.")
    lines += ["", "## Log observations", ""]
    for finding in report["engineLogFindings"]:
        lines.append(f"- **{safe(finding['category'])}**: {finding['matchingLines']} matching lines in {safe(finding['source'])}.")
        for example in finding["examples"]:
            lines.append(f"  Line {example['line']}: {safe(example['excerpt'])}")
    if not report["engineLogFindings"]:
        lines.append("No issue pattern found in available engine logs; this is not proof of application health.")
    lines += ["", "## Coverage and limitations", "",
              "- Available evidence: " + ", ".join(f"{key}={value}" for key, value in report["coverage"].items())]
    lines.extend(f"- {safe(item['source'])}: {safe(item['message'])}" for item in report["problems"])
    lines.extend(f"- Duplicate content: {safe(item['source'])}; already represented by {safe(item['sameContentAs'])}."
                 for item in report["duplicates"])
    lines.extend(f"- {safe(note)}" for note in report["notes"])
    lines += ["", "Correlate these observations with the run's criteria, client/engine/server metrics and application logs.",
              "Root-cause hypotheses require additional evidence; no remediation was executed.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = Analyzer().analyze(args.input)
        result = json.dumps(report, indent=2, ensure_ascii=True) + "\n" if args.format == "json" else markdown(report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result, encoding="utf-8")
            print(f"Wrote {'partial ' if not report['complete'] else ''}analysis to {args.output}")
        else:
            print(result, end="")
        if not report["complete"]:
            parser.exit(2, "Evidence analysis is partial; inspect problems in the report.\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Artifact analysis failed: {exc}\n")


if __name__ == "__main__":
    main()
