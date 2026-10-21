from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from tools.cli_demo.commands import (
    INSIGHTS_API_VERSION, LOAD_TEST_AUDIENCE, build_commands, insights_view, load_endpoint, script_for,
)
from tools.cli_demo.server import App, CliRunner
from tools.cli_demo.test_demo import AI_INSIGHTS, CONFIG, LOAD_ENDPOINT, RUN, SUBSCRIPTION, fake_runner, wait_job


def resolve_endpoint(app, config=CONFIG):
    preview = app.preview("resolve-load-endpoint", asdict(config))
    app.start(preview["id"], False)
    job = wait_job(app, preview["id"])
    if job["state"] != "succeeded":
        raise AssertionError(job["error"])
    return job


class InsightsContractTests(unittest.TestCase):
    def test_read_only_rest_plan_uses_the_published_route_version_and_token_audience(self):
        commands = build_commands("ai-insights", CONFIG, Path("output"))
        self.assertEqual([item.artifact for item in commands], ["run.json", "ai-insights.json"])
        args = commands[-1].args
        self.assertEqual(args[:3], ("rest", "--method", "get"))
        self.assertEqual(args[args.index("--url") + 1],
                         f"{LOAD_ENDPOINT}/test-runs/{CONFIG.runId}/insights/latest?api-version={INSIGHTS_API_VERSION}")
        self.assertEqual(args[args.index("--resource") + 1], LOAD_TEST_AUDIENCE)
        self.assertEqual(args[args.index("--subscription") + 1], SUBSCRIPTION)
        self.assertNotIn("insights:generate", " ".join(args))
        for shell in ("powershell", "bash"):
            script = script_for(commands, shell)
            self.assertIn("az rest --method get", script)
            self.assertIn(f"api-version={INSIGHTS_API_VERSION}", script)
            self.assertNotIn("Bearer", script)

    def test_endpoint_discovery_reads_the_selected_arm_resource(self):
        command = build_commands("resolve-load-endpoint", CONFIG, Path("output"))[0]
        self.assertEqual(command.args[:2], ("resource", "show"))
        self.assertIn(CONFIG.load_resource_id(), command.args)
        self.assertIn("{id:id,dataPlaneURI:properties.dataPlaneURI}", command.args)

    def test_normalizes_only_public_cloud_service_endpoints(self):
        self.assertEqual(load_endpoint(LOAD_ENDPOINT), LOAD_ENDPOINT)
        self.assertEqual(load_endpoint(LOAD_ENDPOINT.removeprefix("https://")), LOAD_ENDPOINT)
        self.assertEqual(load_endpoint(LOAD_ENDPOINT.upper() + "/"), LOAD_ENDPOINT)
        for value in (None, 42, "", "http://" + LOAD_ENDPOINT[8:], "https://example.invalid",
                      LOAD_ENDPOINT + ".evil.invalid", LOAD_ENDPOINT + ":443", LOAD_ENDPOINT + "/path",
                      LOAD_ENDPOINT + "?token=secret", LOAD_ENDPOINT + "#fragment", LOAD_ENDPOINT + "\n",
                      LOAD_ENDPOINT.replace("https://", "https://@"),
                      LOAD_ENDPOINT.replace("https://", "https://user:secret@")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                load_endpoint(value)

    def test_columns_and_keyed_rows_preserve_summary_recommendations_and_unlisted_fields(self):
        view = insights_view(AI_INSIGHTS)
        self.assertEqual(view["status"], "Succeeded")
        self.assertEqual(view["version"], 2)
        self.assertEqual(view["rows"], [{"id": key, "values": value} for key, value in AI_INSIGHTS["rows"].items()])
        self.assertIn("AdditionalContext", view["columns"])
        self.assertEqual(view["rows"][0]["values"]["AdditionalContext"], "")
        self.assertIn("Summary", view["rows"][1]["values"])

    def test_pending_failed_canceled_and_unknown_states_are_not_changed_to_success(self):
        for status in ("NotStarted", "Running", "Failed", "Canceled", "NewServiceState"):
            with self.subTest(status=status):
                view = insights_view({"status": status})
                self.assertEqual(view["status"], status)
                self.assertEqual(view["rows"], [])
                self.assertIsNone(view["version"])
        self.assertEqual(insights_view({})["status"], "Unknown")

    def test_malformed_and_legacy_responses_fail_explicitly(self):
        for payload in (None, [], {"error": {"code": "NotFound"}}, {"status": None},
                        {"version": True}, {"version": "2"}, {"columns": "Title"},
                        {"columns": [{"name": "Title"}]}, {"rows": []},
                        {"rows": {"1": []}}, {"rows": {"1": {"Title": 2}}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                insights_view(payload)

    def test_rest_execution_refuses_mismatched_cli_context(self):
        command = build_commands("ai-insights", CONFIG, Path("output"))[-1]
        wrong = subprocess.CompletedProcess([], 0, '"00000000-0000-0000-0000-000000000002"', "")
        with patch("tools.cli_demo.server.azure_launcher", return_value=["az"]):
            with patch("tools.cli_demo.server.subprocess.run", return_value=wrong) as run:
                with self.assertRaisesRegex(ValueError, "CLI context mismatch"):
                    CliRunner()(command)
        run.assert_called_once()

    def test_rest_execution_uses_existing_cli_auth_without_invented_headers(self):
        command = build_commands("ai-insights", CONFIG, Path("output"))[-1]
        account = subprocess.CompletedProcess([], 0, json.dumps(SUBSCRIPTION), "")
        response = subprocess.CompletedProcess([], 0, json.dumps(AI_INSIGHTS), "")
        with patch("tools.cli_demo.server.azure_launcher", return_value=["az"]):
            with patch("tools.cli_demo.server.subprocess.run", side_effect=[account, response]) as run:
                self.assertIs(CliRunner()(command), response)
        self.assertEqual(run.call_args.args[0], ["az", *command.args])
        self.assertFalse(run.call_args.kwargs["shell"])


class InsightsJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = App(Path(self.temp.name), fake_runner)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_preview_never_discovers_or_trusts_a_user_supplied_endpoint(self):
        with patch.object(self.app, "runner") as runner:
            with self.assertRaisesRegex(ValueError, "Resolve"):
                self.app.preview("ai-insights", asdict(CONFIG))
            preview = self.app.preview("resolve-load-endpoint", asdict(CONFIG))
            runner.assert_not_called()
        self.assertFalse(Path(preview["artifactDirectory"]).exists())

    def test_resource_bound_discovery_and_retrieval_persist_the_service_json(self):
        resolved = resolve_endpoint(self.app)
        self.assertEqual(resolved["commands"][0]["loadEndpoint"], LOAD_ENDPOINT)
        with patch.object(self.app, "runner", side_effect=fake_runner) as runner:
            preview = self.app.preview("ai-insights", asdict(CONFIG))
            runner.assert_not_called()
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "succeeded", job["error"])
        self.assertEqual(runner.call_count, 2)
        entry = job["commands"][-1]
        self.assertEqual(entry["data"], AI_INSIGHTS)
        self.assertEqual(entry["insights"], insights_view(AI_INSIGHTS))
        path = Path(job["artifactDirectory"]) / "ai-insights.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), AI_INSIGHTS)
        reopened = App(Path(self.temp.name), fake_runner)
        try:
            self.assertEqual(reopened.job(job["id"])["commands"][-1]["insights"], entry["insights"])
            with self.assertRaisesRegex(ValueError, "Resolve"):
                reopened.preview("ai-insights", asdict(CONFIG))
        finally:
            reopened.close()

    def test_changed_resource_or_forged_endpoint_requires_fresh_resolution(self):
        resolve_endpoint(self.app)
        for changes in ({"resourceGroup": "another-rg"}, {"loadTestResource": "another-load"},
                        {"subscription": "00000000-0000-0000-0000-000000000002"},
                        {"loadTestEndpoint": LOAD_ENDPOINT.replace("eastus", "westus")}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "Resolve"):
                self.app.preview("ai-insights", asdict(replace(CONFIG, **changes)))
        self.assertTrue(self.app.preview("ai-insights", asdict(replace(CONFIG, runId="another-run"))))

    def test_invalid_discovery_clears_old_verified_endpoints(self):
        for data in ({"id": CONFIG.load_resource_id(), "dataPlaneURI": "https://example.invalid"},
                     {"id": CONFIG.load_resource_id() + "-wrong", "dataPlaneURI": LOAD_ENDPOINT}):
            resolve_endpoint(self.app)
            with patch.object(self.app, "runner", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")):
                preview = self.app.preview("resolve-load-endpoint", asdict(CONFIG))
                self.app.start(preview["id"], False)
                job = wait_job(self.app, preview["id"])
            self.assertEqual(job["state"], "failed")
            with self.assertRaisesRegex(ValueError, "Resolve"):
                self.app.preview("ai-insights", asdict(CONFIG))

    def test_wrong_incomplete_or_malformed_run_metadata_stops_before_rest(self):
        resolve_endpoint(self.app)
        for data in ({**RUN, "testId": "another-test"}, {**RUN, "testRunId": "another-run"},
                     {**RUN, "status": "EXECUTING"}, None):
            with patch.object(self.app, "runner", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(data), "")) as runner:
                preview = self.app.preview("ai-insights", asdict(CONFIG))
                self.app.start(preview["id"], False)
                job = wait_job(self.app, preview["id"])
            self.assertEqual(job["state"], "failed", data)
            runner.assert_called_once()
            self.assertNotIn("ai-insights.json", {file["name"] for file in job["files"]})

    def test_service_errors_remain_explicit_and_do_not_publish_empty_insights(self):
        resolve_endpoint(self.app)
        for status in (401, 403, 404, 429, 500):
            def failed_rest(command):
                if command.artifact == "ai-insights.json":
                    return subprocess.CompletedProcess([], 1, "", f"ERROR: HTTP {status} - service denied request")
                return fake_runner(command)

            with self.subTest(status=status), patch.object(self.app, "runner", side_effect=failed_rest) as runner:
                preview = self.app.preview("ai-insights", asdict(CONFIG))
                self.app.start(preview["id"], False)
                job = wait_job(self.app, preview["id"])
                self.assertEqual(job["state"], "failed")
                self.assertIn(str(status), job["commands"][-1]["stderr"])
                self.assertNotIn("insights", job["commands"][-1])
                self.assertEqual(runner.call_count, 2)

    def test_generation_status_is_distinct_from_successful_retrieval(self):
        resolve_endpoint(self.app)
        for status in ("NotStarted", "Running", "Failed", "Canceled"):
            def response(command):
                if command.artifact == "ai-insights.json":
                    return subprocess.CompletedProcess([], 0, json.dumps({"status": status}), "")
                return fake_runner(command)

            with self.subTest(status=status), patch.object(self.app, "runner", side_effect=response):
                preview = self.app.preview("ai-insights", asdict(CONFIG))
                self.app.start(preview["id"], False)
                job = wait_job(self.app, preview["id"])
                self.assertEqual(job["state"], "succeeded", job["error"])
                self.assertEqual(job["commands"][-1]["insights"]["status"], status)
                self.assertEqual(job["commands"][-1]["insights"]["rows"], [])

    def test_unexpected_schema_keeps_json_evidence_and_reports_failure(self):
        resolve_endpoint(self.app)

        def response(command):
            if command.artifact == "ai-insights.json":
                return subprocess.CompletedProcess([], 0, '{"rows": ["unexpected"]}', "")
            return fake_runner(command)

        with patch.object(self.app, "runner", side_effect=response):
            preview = self.app.preview("ai-insights", asdict(CONFIG))
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("keyed objects", job["error"])
        self.assertTrue((Path(job["artifactDirectory"]) / "ai-insights.json").is_file())
        self.assertNotIn("insights", job["commands"][-1])


if __name__ == "__main__":
    unittest.main()
