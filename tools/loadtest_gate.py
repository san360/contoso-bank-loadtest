"""Shared fail-closed interpretation of Azure Load Testing run JSON."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Verdict:
    outcome: str
    reason: str
    criteria: list[dict[str, Any]] = field(default_factory=list)
    samples: float | None = None
    p95_ms: float | None = None
    error_pct: float | None = None

    @property
    def exit_code(self) -> int:
        return {"passed": 0, "performance_failed": 1}.get(self.outcome, 2)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def evaluate(run: dict[str, Any]) -> Verdict:
    status = run.get("status")
    if status != "DONE":
        terminal = status in {"FAILED", "CANCELLED", "CANCELED", "STOPPED"}
        return Verdict(
            "unjudgeable" if terminal else "pending",
            f"Run status is {status!r}, not DONE",
        )
    container = run.get("passFailCriteria")
    metrics = container.get("passFailMetrics") if isinstance(container, dict) else None
    if not isinstance(metrics, dict) or not metrics:
        return Verdict("unjudgeable", "No client failure criteria on the completed run")
    criteria = list(metrics.values())
    if not all(isinstance(item, dict) for item in criteria):
        return Verdict("unjudgeable", "Malformed client failure criteria")
    aggregate = run.get("testResult")
    if aggregate not in {"PASSED", "FAILED"}:
        return Verdict("pending", f"Aggregate verdict has not settled: {aggregate!r}")
    if not all(
        str(item.get("result", "")).lower() in {"passed", "failed"}
        and number(item.get("actualValue")) is not None
        and number(item.get("value")) is not None
        for item in criteria
    ):
        return Verdict("pending", "Per-criterion verdicts or actual values have not settled")
    statistics = run.get("testRunStatistics")
    total = statistics.get("Total") if isinstance(statistics, dict) else None
    if not isinstance(total, dict):
        total = {}
    samples = number(total.get("sampleCount"))
    p95 = number(total.get("pct2ResTime"))
    errors = number(total.get("errorPct"))
    if samples is None or samples <= 0 or p95 is None or errors is None:
        return Verdict("pending", "Nonempty client statistics have not settled")
    if p95 < 0 or not 0 <= errors <= 100:
        return Verdict("unjudgeable", "Client statistics are outside their valid ranges")
    details = dict(criteria=criteria, samples=samples, p95_ms=p95, error_pct=errors)
    if any(str(item["result"]).lower() == "failed" for item in criteria):
        return Verdict("performance_failed", "One or more settled criteria breached", **details)
    if aggregate == "FAILED":
        return Verdict("unjudgeable", "Aggregate FAILED without a measured criterion breach", **details)
    return Verdict("passed", "Aggregate and every measured criterion passed", **details)


def check_demo_contract(verdict: Verdict) -> None:
    """Reject missing, extra, weakened, request-specific, or different demo criteria."""
    expected = {("p95", "response_time_ms", ">", 500.0), ("percentage", "error", ">", 1.0)}
    actual = {
        (
            str(item.get("aggregate", "")).lower(),
            str(item.get("clientMetric", "")).lower(),
            item.get("condition"),
            number(item.get("value")),
        )
        for item in verdict.criteria
        if not item.get("requestName")
    }
    if len(verdict.criteria) != 2 or actual != expected:
        raise ValueError(f"Run criteria do not match the fixed transaction gate: {actual!r}")
    for item in verdict.criteria:
        measured_failure = number(item["actualValue"]) > number(item["value"])
        if measured_failure != (str(item["result"]).lower() == "failed"):
            raise ValueError("A criterion's measured value contradicts its verdict")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_json", type=Path)
    parser.add_argument("--demo-contract", action="store_true")
    args = parser.parse_args()
    verdict = evaluate(json.loads(args.run_json.read_text(encoding="utf-8")))
    if args.demo_contract and verdict.outcome in {"passed", "performance_failed"}:
        check_demo_contract(verdict)
    print(json.dumps(verdict.as_dict(), indent=2))
    return verdict.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
