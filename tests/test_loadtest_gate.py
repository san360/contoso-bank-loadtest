import copy
import unittest

from tools.loadtest_gate import check_demo_contract, evaluate


def settled(result="PASSED", latency=75):
    return {
        "status": "DONE",
        "testResult": result,
        "passFailCriteria": {
            "passFailMetrics": {
                "latency": {
                    "aggregate": "p95", "clientMetric": "response_time_ms",
                    "condition": ">", "value": 500, "actualValue": latency,
                    "result": "failed" if latency > 500 else "passed",
                },
                "errors": {
                    "aggregate": "percentage", "clientMetric": "error",
                    "condition": ">", "value": 1, "actualValue": 0,
                    "result": "passed",
                },
            }
        },
        "testRunStatistics": {
            "Total": {"sampleCount": 1000, "pct2ResTime": latency, "errorPct": 0}
        },
    }


class LoadTestGateTests(unittest.TestCase):
    def test_only_positive_settled_proof_passes(self):
        verdict = evaluate(settled())
        self.assertEqual(verdict.exit_code, 0)
        check_demo_contract(verdict)

    def test_done_is_not_passed(self):
        run = settled()
        run["testResult"] = "NOT_APPLICABLE"
        self.assertEqual(evaluate(run).outcome, "pending")
        del run["passFailCriteria"]["passFailMetrics"]["latency"]["result"]
        run["testResult"] = "PASSED"
        self.assertNotEqual(evaluate(run).exit_code, 0)

    def test_infrastructure_and_cancelled_and_criteria_less_fail_closed(self):
        for status in ("FAILED", "CANCELLED", "STOPPED", "UNKNOWN", "EXECUTING"):
            run = settled()
            run["status"] = status
            self.assertNotEqual(evaluate(run).exit_code, 0)
        run = settled()
        run["passFailCriteria"] = {}
        self.assertEqual(evaluate(run).outcome, "unjudgeable")

    def test_measured_regression_and_pessimistic_aggregate(self):
        for aggregate in ("FAILED", "PASSED"):
            verdict = evaluate(settled(aggregate, 1150))
            self.assertEqual(verdict.outcome, "performance_failed")
            check_demo_contract(verdict)
        self.assertEqual(evaluate(settled("FAILED", 70)).outcome, "unjudgeable")

    def test_incomplete_statistics_or_values_cannot_pass(self):
        for field, value in (("sampleCount", 0), ("errorPct", None), ("pct2ResTime", "NaN")):
            run = settled()
            run["testRunStatistics"]["Total"][field] = value
            self.assertNotEqual(evaluate(run).exit_code, 0)
        run = settled()
        del run["passFailCriteria"]["passFailMetrics"]["latency"]["actualValue"]
        self.assertNotEqual(evaluate(run).exit_code, 0)

    def test_demo_rejects_weakened_or_missing_or_contradictory_criteria(self):
        changes = (
            {"value": 5000},
            {"requestName": "OtherRequest"},
            {"clientMetric": "latency"},
            {"actualValue": 1200},
        )
        for change in changes:
            run = copy.deepcopy(settled())
            run["passFailCriteria"]["passFailMetrics"]["latency"].update(change)
            with self.assertRaises(ValueError):
                check_demo_contract(evaluate(run))


if __name__ == "__main__":
    unittest.main()
