from __future__ import annotations

import gzip
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_helper(skill, script):
    path = ROOT / ".github" / "skills" / skill / "scripts" / script
    spec = importlib.util.spec_from_file_location(skill.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


jmx = load_helper("jmx-to-locust", "inspect_jmx.py")
analysis = load_helper("analyze-load-test-artifacts", "analyze_artifacts.py")


def archive_bytes(files):
    handle = io.BytesIO()
    with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return handle.getvalue()


class JmxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def inspect(self, text):
        path = self.root / "test.jmx"
        path.write_text(text, encoding="utf-8")
        return jmx.inspect(path)

    def test_real_plan_inventory_preserves_requests_and_csv(self):
        path = ROOT / "loadtests" / "jmeter" / "contoso-bank.jmx"
        before = path.read_bytes()
        result = jmx.inspect(path)
        self.assertEqual(len(result["samplers"]), 5)
        self.assertEqual(result["csvDataSets"][0]["shareMode"], "shareMode.all")
        self.assertIn("duration", result["propertyReferences"])
        self.assertIn("__threadNum", result["functions"])
        self.assertIn("ThroughputController", {item["type"] for item in result["reviewRequired"]})
        self.assertEqual(result["samplers"][0]["scope"][-1], "Journey - Balance Enquiry (60%)")
        self.assertEqual(path.read_bytes(), before)

    def test_disabled_parent_excludes_entire_execution_subtree(self):
        result = self.inspect("""<jmeterTestPlan><hashTree>
          <ThreadGroup enabled="false" testname="disabled"/><hashTree>
            <HTTPSamplerProxy testname="never"/><hashTree/>
          </hashTree>
        </hashTree></jmeterTestPlan>""")
        self.assertEqual(result["samplers"], [])
        self.assertEqual(len(result["disabledSubtrees"]), 1)

    def test_sampler_siblings_and_scoped_children_are_distinguished(self):
        result = self.inspect("""<jmeterTestPlan><hashTree>
          <ThreadGroup testname="users"/><hashTree>
            <HTTPSamplerProxy testname="first"/><hashTree>
              <JSONPostProcessor testname="extract"/><hashTree/>
            </hashTree>
            <HTTPSamplerProxy testname="second"/><hashTree/>
          </hashTree>
        </hashTree></jmeterTestPlan>""")
        extractor = next(item for item in result["elements"] if item["type"] == "JSONPostProcessor")
        self.assertEqual(extractor["scope"], ["users", "first"])
        self.assertEqual(result["samplers"][1]["scope"], ["users"])

    def test_unknown_plugins_require_manual_translation(self):
        result = self.inspect("""<jmeterTestPlan><hashTree>
          <custom.Plugin testname="plugin"/><hashTree/>
        </hashTree></jmeterTestPlan>""")
        self.assertIn("Unknown/plugin", result["reviewRequired"][0]["reason"])

    def test_rejects_entity_declarations_and_malformed_pairs(self):
        for text in ("""<!DOCTYPE x [<!ENTITY a "bomb">]><jmeterTestPlan><hashTree/></jmeterTestPlan>""",
                     "<jmeterTestPlan><hashTree><ThreadGroup/></hashTree></jmeterTestPlan>"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.inspect(text)

    def test_inventory_does_not_export_header_or_body_literals(self):
        result = self.inspect("""<jmeterTestPlan><hashTree>
          <HeaderManager testname="headers"><stringProp name="Header.value">Bearer PRIVATE</stringProp></HeaderManager><hashTree/>
        </hashTree></jmeterTestPlan>""")
        self.assertNotIn("PRIVATE", json.dumps(result))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def report(self):
        return analysis.Analyzer().analyze(self.root)

    def test_jmeter_csv_percentiles_unknown_outcomes_and_denominator(self):
        self.write("results.csv", """timeStamp,elapsed,label,responseCode,success
1000,10,health,200,true
2000,20,health,200,true
3000,30,health,500,false
4000,40,health,200,true
5000,500,health,200,
""")
        result = self.report()["requestFiles"][0]["summary"]
        self.assertEqual(result["requests"], 5)
        self.assertEqual(result["failures"], 1)
        self.assertEqual(result["unknownOutcomes"], 1)
        self.assertEqual(result["errorRatePctOfKnownOutcomes"], 25)
        self.assertEqual(result["p50Ms"], 30)
        self.assertEqual(result["p95Ms"], 500)
        self.assertIsNotNone(result["requestsPerObservedSecond"])

    def test_duplicate_archive_and_extracted_csv_are_not_counted_twice(self):
        csv = "elapsed,label,responseCode,success\n10,health,200,true\n"
        self.write("results.csv", csv)
        self.write("results.zip", archive_bytes({"results.csv": csv}))
        report = self.report()
        self.assertEqual(len(report["requestFiles"]), 1)
        self.assertTrue(report["duplicates"])

    def test_nested_bundle_keeps_engine_member_provenance(self):
        csv = "elapsed,label,responseCode,success\n10,health,200,true\n"
        self.write("evidence.zip", archive_bytes({
            "files/results.zip": archive_bytes({"engine1/results.csv": csv}),
            "files/logs.zip": archive_bytes({"engine1/jmeter.log": "ERROR Read timed out\n"}),
        }))
        report = self.report()
        self.assertTrue(report["complete"], report["problems"])
        self.assertIn("!files/results.zip!engine1/results.csv", report["requestFiles"][0]["source"])
        self.assertEqual(report["engineLogFindings"][0]["examples"][0]["line"], 1)

    def test_zip_member_traversal_is_never_extracted(self):
        self.write("bad.zip", archive_bytes({"../outside.csv": "elapsed\n10\n"}))
        report = self.report()
        self.assertFalse(report["complete"])
        self.assertIn("Unsafe archive", report["problems"][0]["message"])
        self.assertFalse((self.root.parent / "outside.csv").exists())

    def test_gzip_expansion_limit_is_explicit(self):
        self.write("large.log.gz", gzip.compress(b"A" * 4096))
        with patch.object(analysis, "MAX_FILE", 1024):
            report = self.report()
        self.assertFalse(report["complete"])
        self.assertIn("limit", report["problems"][0]["message"])

    def test_xml_uses_leaf_samples_not_parent_plus_children(self):
        self.write("results.jtl", """<testResults>
          <sample t="100" s="true" lb="transaction" rc="200">
            <httpSample t="10" s="true" lb="first" rc="200" ts="1000"/>
            <httpSample t="20" s="false" lb="second" rc="500" ts="2000"/>
          </sample>
        </testResults>""")
        report = self.report()
        self.assertEqual(report["requestFiles"][0]["summary"]["requests"], 2)
        self.assertEqual(report["requestFiles"][0]["summary"]["failures"], 1)
        self.assertTrue(any("composite" in note for note in report["notes"]))

    def test_xml_entities_are_rejected(self):
        self.write("results.jtl", '<!DOCTYPE x [<!ENTITY secret "no">]><testResults/>')
        self.assertFalse(self.report()["complete"])

    def test_locust_aggregate_is_not_added_to_endpoint_totals(self):
        self.write("final_stats.csv", """Type,Name,Request Count,Failure Count,Average Response Time,50%,95%,99%
GET,/one,10,1,20,10,30,40
GET,/two,20,2,40,20,60,80
,Aggregated,30,3,33.3,20,60,80
""")
        data = self.report()["requestFiles"][0]
        self.assertEqual(data["summary"]["requests"], 30)
        self.assertEqual(data["summary"]["p95Ms"], 60)
        self.assertEqual(len(data["requests"]), 2)

    def test_locust_missing_aggregate_does_not_average_percentiles(self):
        self.write("final_stats.csv", """Type,Name,Request Count,Failure Count,95%
GET,/one,10,0,30
GET,/two,10,0,90
""")
        result = self.report()["requestFiles"][0]["summary"]
        self.assertEqual(result["requests"], 20)
        self.assertIsNone(result["p95Ms"])

    def test_locust_history_is_not_summed_as_final_stats(self):
        self.write("stats_history.csv", "Timestamp,User Count,Total Request Count\n1000,5,100\n2000,5,200\n")
        self.assertEqual(self.report()["requestFiles"], [])

    def test_sample_limits_are_reported_for_raw_and_locust_files(self):
        for name, content in (
            ("raw.csv", "elapsed,label,success\n10,one,true\n20,two,true\n"),
            ("locust.csv", "Name,Request Count,Failure Count\none,1,0\ntwo,2,0\n"),
        ):
            with self.subTest(name=name):
                path = self.write(name, content)
                with patch.object(analysis, "MAX_SAMPLES", 1):
                    report = analysis.Analyzer().analyze(path)
                self.assertFalse(report["complete"])
                self.assertIn("limit", report["problems"][0]["message"])

    def test_malformed_and_nonfinite_samples_make_partial_report(self):
        self.write("results.csv", "elapsed,label,success\n10,good,true\nNaN,bad,true\n-1,bad,false\n")
        report = self.report()
        self.assertFalse(report["complete"])
        self.assertEqual(report["requestFiles"][0]["summary"]["requests"], 1)

    def test_run_criterion_failure_overrides_transient_aggregate_green(self):
        self.write("run.json", json.dumps({
            "testRunId": "one", "status": "DONE", "testResult": "PASSED",
            "passFailCriteria": {"passFailMetrics": {"one": {"result": "failed", "actualValue": 20, "value": 1}}},
        }))
        run = self.report()["runs"][0]
        self.assertEqual(run["performanceConclusion"], "failed")

    def test_done_without_criteria_has_no_performance_verdict(self):
        self.write("run.json", '{"testRunId":"one","status":"DONE","testResult":"PASSED"}')
        self.assertEqual(self.report()["runs"][0]["performanceConclusion"], "no-verdict")

    def test_done_not_applicable_with_configured_criteria_is_unsettled(self):
        self.write("run.json", json.dumps({
            "testRunId": "one", "status": "DONE", "testResult": "NOT_APPLICABLE",
            "passFailCriteria": {"passFailMetrics": {"one": {"clientMetric": "error", "value": 1}}},
        }))
        self.assertEqual(self.report()["runs"][0]["performanceConclusion"], "unsettled")

    def test_only_logs_do_not_invent_requests_or_verdict(self):
        self.write("engine.log", "INFO Started\n")
        report = self.report()
        self.assertEqual(report["runs"], [])
        self.assertEqual(report["requestFiles"], [])
        self.assertTrue(report["coverage"]["engineLogs"])
        self.assertIn("No performance verdict", analysis.markdown(report))

    def test_log_excerpts_are_redacted_with_line_evidence(self):
        self.write("engine.log",
                   "INFO boot\nERROR Read timed out https://x.invalid/a?sig=SECRET Bearer TOPSECRET password=HUNTER\n")
        report = self.report()
        serialized = json.dumps(report)
        for secret in ("sig=SECRET", "TOPSECRET", "HUNTER"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(report["engineLogFindings"][0]["examples"][0]["line"], 2)

    def test_quoted_json_credentials_in_logs_are_redacted(self):
        self.write("engine.log", 'ERROR {"password":"PRIVATE","api_key":"ALSO_PRIVATE"}\n')
        self.assertNotIn("PRIVATE", json.dumps(self.report()))

    def test_autostop_configuration_log_is_not_an_engine_error(self):
        self.write("engine.log",
                   "2026-10-19 14:54:13,038 WARN MALTAutostopListener: "
                   "MALT autostop test started with error rate 90 for 60s.\n")
        self.assertEqual(self.report()["engineLogFindings"], [])

    def test_locust_failure_records_appear_in_markdown(self):
        self.write("failures.csv", 'Method,Name,Error,Occurrences\nGET,health,Read timed out,7\n')
        self.assertIn("Read timed out", analysis.markdown(self.report()))

    def test_metric_json_and_csv_are_not_treated_as_request_samples(self):
        self.write("client-metrics.json", json.dumps([{"data": [{"timestamp": "2026-10-19T10:00:00Z", "value": 10}]}]))
        self.write("client-metrics.csv", "timestamp,namespace,metric,aggregation,value\n2026-10-19T10:00:00Z,LoadTestRunMetrics,ResponseTime,value,10\n")
        report = self.report()
        self.assertEqual(report["requestFiles"], [])
        self.assertEqual(len(report["metricFiles"]), 2)

    def test_utf16_metadata_from_windows_powershell_is_readable(self):
        self.write("run.json", '{"testRunId":"one","status":"DONE"}'.encode("utf-16"))
        self.assertEqual(self.report()["runs"][0]["status"], "DONE")

    def test_missing_input_fails_explicitly(self):
        with self.assertRaises(FileNotFoundError):
            analysis.Analyzer().analyze(self.root / "missing.zip")


if __name__ == "__main__":
    unittest.main()
