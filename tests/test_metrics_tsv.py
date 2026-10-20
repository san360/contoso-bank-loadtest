import copy
import csv
import io
import json
import os
import shutil
import subprocess
import unittest

import yaml

from tools.gitlab_demo.common import ROOT, read_json


class MetricsTsvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jq = os.getenv("JQ") or shutil.which("jq")
        if not cls.jq:
            raise RuntimeError("Metric TSV tests require native jq (runner includes it); set JQ to its local executable.")
        cls.fixture = read_json(ROOT / "tests" / "fixtures" / "gitlab-alt-metrics.json")

    def project(self, view, payload, scope=None):
        original = json.dumps(payload, sort_keys=True)
        args = [self.jq, "-r", "--arg", "view", view]
        if scope is not None:
            args += ["--arg", "scope", scope]
        result = subprocess.run(
            [*args, "-f", str(ROOT / "loadtests" / "gitlab-demo" / "metrics-tsv.jq")],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
        )
        self.assertEqual(json.dumps(payload, sort_keys=True), original)
        return list(csv.reader(io.StringIO(result.stdout), delimiter="\t"))

    def test_recorded_request_map_uses_real_field_names_and_units(self):
        rows = self.project("requests", self.fixture["run"])
        self.assertEqual(rows[0], ["request", "samples", "p95_ms", "error_percent", "throughput_rps"])
        self.assertEqual([row[0] for row in rows[1:]], ["GetTransactions", "Total"])
        for row in rows[1:]:
            self.assertEqual(row[1:4], ["5336", "310", "0"])
            self.assertAlmostEqual(float(row[4]), 56.16842105263158)
        missing = copy.deepcopy(self.fixture["run"])
        del missing["testRunStatistics"]["Total"]["throughput"]
        self.assertEqual(self.project("requests", missing)[2][4], "N/A")

    def test_recorded_client_map_has_explicit_units_and_no_invented_error_series(self):
        rows = self.project("client", self.fixture["client"])
        self.assertEqual(rows[0], ["timestamp_utc", "scope", "metric", "value", "unit", "status"])
        self.assertIn(["N/A", "client", "Errors", "N/A", "count", "no samples"], rows)
        self.assertIn(["2026-10-19T20:39:50.000Z", "client", "ResponseTime", "85", "ms", "measured"], rows)
        self.assertIn(["2026-10-19T20:39:50.000Z", "client", "TotalRequests", "98", "count", "measured"], rows)
        self.assertIn(["2026-10-19T20:39:50.000Z", "client", "VirtualUsers", "3", "users", "measured"], rows)

    def test_recorded_server_zero_and_missing_bucket_are_distinct(self):
        total = self.project("server", self.fixture["staging_total"], "staging")
        self.assertEqual(total[0], ["timestamp_utc", "scope", "metric", "aggregation", "value", "unit", "status"])
        self.assertIn(["2026-10-19T20:39:00Z", "staging_slot", "Requests", "total", "0", "Count", "measured"], total)
        average = self.project("server", self.fixture["staging_average"], "staging")
        response = [row for row in average[1:] if row[2] == "HttpResponseTime"]
        self.assertEqual(response, [["2026-10-19T20:38:00Z", "staging_slot", "HttpResponseTime", "average", "0.01", "Seconds", "measured"]])
        plan = self.project("server", self.fixture["shared_plan"], "shared_plan")
        self.assertTrue(all(row[1] == "shared_plan" for row in plan[1:]))
        self.assertIn(["2026-10-19T20:38:00Z", "shared_plan", "CpuPercentage", "average", "21", "Percent", "measured"], plan)
        self.assertIn(["2026-10-19T20:38:00Z", "shared_plan", "HttpQueueLength", "average", "0", "Count", "measured"], plan)

    def test_null_samples_are_never_replaced_with_zero(self):
        client = {"Errors": [{"data": [{"timestamp": "empty", "value": None}]}]}
        self.assertEqual(self.project("client", client)[1], ["N/A", "client", "Errors", "N/A", "count", "no samples"])
        server = {"value": [{"name": {"value": "Requests"}, "unit": "Count", "timeseries": [{"data": [{"timeStamp": "empty", "total": None}]}]}]}
        self.assertEqual(self.project("server", server, "staging")[1], ["N/A", "staging_slot", "Requests", "N/A", "N/A", "Count", "no samples"])
        client["Errors"][0]["data"][0]["value"] = 0
        self.assertEqual(self.project("client", client)[1][3], "0")

    def test_recorded_definitions_label_scope_and_do_not_claim_samples(self):
        rows = self.project("definitions", self.fixture["definitions"])
        self.assertEqual(rows[0], ["scope", "name", "namespace", "aggregation", "resource_id", "unit"])
        self.assertEqual(rows[1][:4], ["shared_plan", "CpuPercentage", "Microsoft.Web/serverFarms", "Average"])
        self.assertTrue(rows[1][4].endswith("/serverfarms/plan-gitlab-demo"))
        self.assertEqual(rows[2][:4], ["staging_slot", "HttpResponseTime", "Microsoft.Web/sites/slots", "Average"])
        self.assertTrue(rows[2][4].endswith("/slots/staging"))

    def test_primary_and_advanced_use_same_console_projection_and_keep_json(self):
        document = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        for name in ("cli_walkthrough", "performance_gate"):
            script = "\n".join(document[name]["script"])
            for view in ("requests", "client", "server", "definitions", "components"):
                self.assertIn("--arg view " + view, script)
            self.assertIn("-o json > demo-evidence/client-metric-series.json", script)
            self.assertIn("-o json > demo-evidence/server-metric-definitions.json", script)
            self.assertEqual(document[name]["artifacts"]["when"], "always")
            self.assertEqual(document[name]["resource_group"], "contoso-gitlab-demo-staging")
        self.assertNotIn("python", "\n".join(document["cli_walkthrough"]["script"]).lower())


if __name__ == "__main__":
    unittest.main()
