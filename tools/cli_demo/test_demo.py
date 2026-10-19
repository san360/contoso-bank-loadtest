from __future__ import annotations

import csv
import http.client
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from tools.cli_demo.commands import (
    ACTIONS, Config, build_commands, csv_export, metric_rows, redact, run_verdict, shell_command,
)
from tools.cli_demo.server import App, CliRunner, DemoServer, azure_launcher

SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"
RESOURCE = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/demo-rg/providers/Microsoft.Web/sites/demo-app"
CONFIG = Config(
    subscription=SUBSCRIPTION, resourceGroup="demo-rg", loadTestResource="demo-load",
    testId="cli-demo-smoke", runId="cli-unit-test", targetUrl="https://example.invalid/health?a=1&b=2",
    serverResourceId=RESOURCE, serverResourceName="demo-app", serverResourceType="Microsoft.Web/sites",
    serverResourceKind="app", serverMetric="CpuTime",
    serverMetricId=f"{RESOURCE}/providers/microsoft.insights/metricdefinitions/CpuTime",
    serverNamespace="Microsoft.Web/sites", serverAggregation="Total",
    startTime="2026-10-19T10:00:00Z", endTime="2026-10-19T10:01:00Z",
)
RUN = {
    "testRunId": CONFIG.runId, "testId": CONFIG.testId, "status": "DONE", "testResult": "PASSED",
    "startDateTime": CONFIG.startTime, "endDateTime": CONFIG.endTime,
    "passFailCriteria": {"passFailMetrics": {"one": {"result": "passed", "actualValue": 0, "value": 1}}},
    "artifacts": {"url": "https://example.invalid/results.zip?sv=1&sig=supersecret"},
    "testRunStatistics": {"HealthCheck": {"sampleCount": 20, "meanResTime": 10, "errorPct": 0}},
}


def fake_runner(command):
    if command.download:
        directory = Path(command.args[command.args.index("--path") + 1])
        directory.mkdir(parents=True, exist_ok=True)
        name = "logs.zip" if "--log" in command.args else "results.zip"
        with zipfile.ZipFile(directory / name, "w") as archive:
            if "--log" in command.args:
                archive.writestr("engine.log", "INFO Started\nERROR Read timed out\n")
            else:
                archive.writestr("results.csv", "timeStamp,elapsed,label,responseCode,success\n1000,10,HealthCheck,200,true\n")
        data = None
    elif command.artifact == "run.json":
        data = RUN
    elif command.metric:
        data = [{"data": [{"timestamp": "2026-10-19T10:00:00Z", "value": 10}]}]
    else:
        data = {"ok": True}
    return subprocess.CompletedProcess(command.args, 0, json.dumps(data) if data is not None else "", "")


def wait_job(app, id_):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = app.job(id_)
        if job["state"] not in {"queued", "running"}:
            return job
        time.sleep(.01)
    raise AssertionError("Local fake command did not finish.")


class CommandTests(unittest.TestCase):
    def test_every_advertised_action_builds_only_azure_commands(self):
        for action in ACTIONS:
            with self.subTest(action=action.id):
                commands = build_commands(action.id, CONFIG, Path("artifacts"))
                self.assertTrue(commands)
                for command in commands:
                    if action.id == "select-subscription":
                        self.assertTrue(action.confirmation)
                        self.assertEqual(command.args[:2], ("account", "set"))
                    else:
                        self.assertNotIn("account set", " ".join(command.args))
                    self.assertIn("--output", command.args)
                    self.assertNotIn("--debug", command.args)

    def test_no_arbitrary_command_or_config_fields(self):
        with self.assertRaisesRegex(ValueError, "Arbitrary"):
            build_commands("shell", CONFIG, Path("output"))
        with self.assertRaisesRegex(ValueError, "Unknown configuration"):
            Config.parse({"command": "whoami"})

    def test_config_validates_types_and_control_characters(self):
        for value in ({"runId": ["not", "a", "string"]}, {"resourceGroup": "rg\nbad"}):
            with self.assertRaises(ValueError):
                Config.parse(value)

    def test_explicit_scoping_and_no_global_subscription_switch(self):
        commands = build_commands("run-test", CONFIG, Path("output"))
        args = commands[0].args
        self.assertIn("--no-wait", args)
        self.assertEqual(args[args.index("--subscription") + 1], SUBSCRIPTION)
        self.assertEqual(args[args.index("--test-run-id") + 1], CONFIG.runId)
        self.assertEqual(args[args.index("--load-test-resource") + 1],
                         f"/subscriptions/{SUBSCRIPTION}/resourceGroups/demo-rg/"
                         "providers/Microsoft.LoadTestService/loadTests/demo-load")

    def test_account_check_shows_actual_active_context(self):
        command = build_commands("account", CONFIG, Path("output"))[0]
        self.assertNotIn("--subscription", command.args)

    def test_small_test_is_fixed_and_target_is_one_argument(self):
        args = build_commands("create-test", CONFIG, Path("output"))[0].args
        self.assertIn("DEMO_PATH=/health?a=1&b=2", args)
        self.assertTrue(any(value.endswith("smoke.yaml") for value in args))
        self.assertNotIn("--engine-instances", args)

    def test_target_rejects_credentials_and_localhost(self):
        for target in ("file:///tmp/test", "https://user:pass@example.invalid/",
                       "https://example.invalid/?api_key=secret", "http://127.0.0.1:8000/",
                       "https://example.invalid:99999/"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                build_commands("create-test", replace(CONFIG, targetUrl=target), Path("output"))

    def test_identifiers_and_resource_names_are_not_options(self):
        for key in ("testId", "runId", "resourceGroup", "loadTestResource"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_commands("run-test", replace(CONFIG, **{key: "--help"}), Path("output"))

    def test_server_values_use_monitor_not_load_metric_namespace(self):
        command = build_commands("server-metrics", CONFIG, Path("output"))[0]
        self.assertEqual(command.args[:3], ("monitor", "metrics", "list"))
        self.assertIn(RESOURCE, command.args)
        self.assertIn("--start-time", command.args)
        self.assertNotIn("ServerMetrics", command.args)

    def test_server_query_requires_actual_time_range(self):
        for changes in ({"startTime": ""}, {"startTime": "2026-10-19T10:00:00"},
                        {"endTime": "2026-10-19T09:00:00Z"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_commands("server-metrics", replace(CONFIG, **changes), Path("output"))

    def test_server_resource_must_match_selected_subscription(self):
        with self.assertRaisesRegex(ValueError, "configured subscription"):
            build_commands("server-metrics", replace(CONFIG, serverResourceId=RESOURCE.replace(
                SUBSCRIPTION, "00000000-0000-0000-0000-000000000002")), Path("output"))

    def test_metric_definition_id_is_discovered_not_invented(self):
        args = build_commands("attach-server-metric", CONFIG, Path("output"))[0].args
        self.assertIn(CONFIG.serverMetricId, args)
        with self.assertRaises(ValueError):
            build_commands("attach-server-metric", replace(CONFIG, serverMetricId="/other/id"), Path("output"))

    def test_evidence_bundle_is_explicit_about_server_values(self):
        commands = build_commands("collect-evidence", CONFIG, Path("output"))
        self.assertEqual(commands[-1].args[:3], ("monitor", "metrics", "list"))
        without = build_commands("collect-evidence", replace(CONFIG, serverResourceId=""), Path("output"))
        self.assertEqual(len(without), 5)
        self.assertFalse(any("--report" in command.args for command in without))

    def test_powershell_and_bash_quoting_preserve_metacharacters(self):
        args = ("test", "a&b", "it's", "$value", r"C:\a b\test.yaml")
        powershell = shell_command(args)
        self.assertIn("'a&b'", powershell)
        self.assertIn("'it''s'", powershell)
        self.assertIn("'$value'", powershell)
        import shlex
        self.assertEqual(shlex.split(shell_command(args, "bash")), ["az", *args])

    def test_windows_launcher_bypasses_cmd(self):
        with patch("tools.cli_demo.server.shutil.which", return_value=r"C:\Azure\CLI2\wbin\az.cmd"):
            with patch("tools.cli_demo.server.Path.is_file", return_value=True):
                launcher = azure_launcher()
        self.assertEqual(launcher, [r"C:\Azure\CLI2\python.exe", "-IBm", "azure.cli"])

    def test_missing_azure_cli_is_actionable(self):
        with patch("tools.cli_demo.server.shutil.which", return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, "not installed"):
                azure_launcher()

    def test_cli_runner_blocks_wrong_default_before_a_load_command(self):
        command = build_commands("list-tests", CONFIG, Path("output"))[0]
        wrong = subprocess.CompletedProcess([], 0, '"00000000-0000-0000-0000-000000000002"', "")
        with patch("tools.cli_demo.server.azure_launcher", return_value=["az"]):
            with patch("tools.cli_demo.server.subprocess.run", return_value=wrong) as run:
                with self.assertRaisesRegex(ValueError, "CLI context mismatch"):
                    CliRunner()(command)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1:3], ["account", "show"])

    def test_cli_runner_uses_matching_context_without_switching_it(self):
        command = build_commands("list-tests", CONFIG, Path("output"))[0]
        account = subprocess.CompletedProcess([], 0, json.dumps(SUBSCRIPTION), "")
        result = subprocess.CompletedProcess([], 0, "[]", "")
        with patch("tools.cli_demo.server.azure_launcher", return_value=["az"]):
            with patch("tools.cli_demo.server.subprocess.run", side_effect=[account, result]) as run:
                self.assertIs(CliRunner()(command), result)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0], ["az", *command.args])
        self.assertFalse(run.call_args_list[1].kwargs["shell"])

    def test_context_selection_is_only_an_explicit_command(self):
        command = build_commands("select-subscription", CONFIG, Path("output"))[0]
        with patch("tools.cli_demo.server.azure_launcher", return_value=["az"]):
            with patch("tools.cli_demo.server.subprocess.run",
                       return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                CliRunner()(command)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1:3], ["account", "set"])


class ExportTests(unittest.TestCase):
    def test_actual_load_cli_response_shape_keeps_metric_context(self):
        data = [{"data": [{"timestamp": "2026-10-19T10:00:00Z", "value": 122}]}]
        rows = list(csv.DictReader(io.StringIO(csv_export(data, "ResponseTime", "LoadTestRunMetrics"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], "122")
        self.assertEqual(rows[0]["metric"], "ResponseTime")

    def test_azure_monitor_response_retains_unit_dimension_and_zero(self):
        data = {"value": [{"name": {"value": "CpuTime"}, "unit": "Seconds",
                           "timeseries": [{"metadatavalues": [{"name": {"value": "Instance"}, "value": "one"}],
                                           "data": [{"timeStamp": CONFIG.startTime, "total": 0, "average": None}]}]}]}
        rows = metric_rows(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 0)
        self.assertEqual(rows[0]["unit"], "Seconds")
        self.assertIn("Instance", rows[0]["dimensions"])

    def test_spreadsheet_formulas_are_escaped(self):
        data = [{"data": [{"timestamp": CONFIG.startTime, "value": 1}]}]
        exported = list(csv.DictReader(io.StringIO(csv_export(data, "=HYPERLINK()"))))
        self.assertEqual(exported[0]["metric"], "'=HYPERLINK()")

    def test_redacts_signed_urls_and_credentials(self):
        result = redact({"password": "secret", "url": "https://x.invalid/a?sv=1&sig=abc",
                         "message": "Bearer abcd123 password=hunter2"})
        self.assertNotIn("hunter2", json.dumps(result))
        self.assertNotIn("sig=abc", json.dumps(result))
        self.assertNotIn("abcd123", json.dumps(result))
        self.assertNotIn("quotedsecret", redact('{"password":"quotedsecret"}'))

    def test_done_is_not_a_pass(self):
        self.assertEqual(run_verdict({"status": "DONE", "testResult": "PASSED"})["label"], "NO VERDICT")
        self.assertEqual(run_verdict(RUN)["label"], "PASSED")
        failed = json.loads(json.dumps(RUN))
        failed["passFailCriteria"]["passFailMetrics"]["one"]["result"] = "failed"
        self.assertEqual(run_verdict(failed)["label"], "FAILED")
        failed["passFailCriteria"]["passFailMetrics"]["one"]["result"] = None
        self.assertEqual(run_verdict(failed)["label"], "SETTLING")
        failed["testResult"] = "NOT_APPLICABLE"
        self.assertEqual(run_verdict(failed)["label"], "SETTLING")

    def test_live_done_not_applicable_transition_keeps_watching_configured_criteria(self):
        run = {"status": "DONE", "testResult": "NOT_APPLICABLE",
               "passFailCriteria": {"passFailMetrics": {
                   "one": {"clientMetric": "error", "aggregate": "percentage", "value": 1},
                   "two": {"clientMetric": "response_time_ms", "aggregate": "avg", "value": 1000},
               }}}
        self.assertEqual(run_verdict(run)["label"], "SETTLING")
        self.assertEqual(run_verdict(run)["kind"], "pending")
        run["passFailCriteria"]["passFailMetrics"] = {}
        self.assertEqual(run_verdict(run)["label"], "NO VERDICT")


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = App(Path(self.temp.name), fake_runner)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_preview_is_not_execution_and_requires_confirmation(self):
        preview = self.app.preview("run-test", asdict(CONFIG))
        self.assertFalse(Path(preview["artifactDirectory"]).exists())
        with self.assertRaisesRegex(ValueError, "Confirm"):
            self.app.start(preview["id"], False)

    def test_preview_runs_once_and_persists_artifacts(self):
        preview = self.app.preview("collect-evidence", asdict(CONFIG))
        self.app.start(preview["id"], True)
        job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "succeeded", job["error"])
        names = {file["name"] for file in job["files"]}
        self.assertTrue({"run.json", "client-metrics.csv", "engine-metrics.json",
                         "server-metrics.csv", "files/logs.zip", "files/results.zip",
                         "manifest.json"}.issubset(names))
        self.assertNotIn("supersecret", (Path(job["artifactDirectory"]) / "run.json").read_text())
        self.assertNotIn("supersecret", job["commands"][0]["stdout"])
        with self.assertRaisesRegex(ValueError, "already executed"):
            self.app.start(preview["id"], True)
        reopened = App(Path(self.temp.name), fake_runner)
        try:
            self.assertEqual(reopened.job(job["id"])["state"], "succeeded")
        finally:
            reopened.close()

    def test_cli_failure_is_visible_and_not_retried(self):
        with patch.object(self.app, "runner", return_value=subprocess.CompletedProcess([], 3, "", "RBAC denied")) as runner:
            preview = self.app.preview("show-run", asdict(CONFIG))
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("code 3", job["error"])
        self.assertIn("RBAC denied", job["commands"][0]["stderr"])
        runner.assert_called_once()

    def test_unexpected_processing_error_never_publishes_success(self):
        with patch.object(self.app, "runner", side_effect=RuntimeError("Unexpected implementation failure")):
            preview = self.app.preview("show-run", asdict(CONFIG))
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("Unexpected local processing error", job["error"])

    def test_empty_metric_is_not_shown_as_zero(self):
        with patch.object(self.app, "runner", return_value=subprocess.CompletedProcess([], 0, "[]", "")):
            preview = self.app.preview("client-metrics", asdict(CONFIG))
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertTrue(job["warnings"])
        self.assertEqual(job["commands"][0]["metricRows"], [])

    def test_success_without_downloaded_files_is_not_reported_as_success(self):
        with patch.object(self.app, "runner", return_value=subprocess.CompletedProcess([], 0, "", "")):
            preview = self.app.preview("download-logs", asdict(CONFIG))
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("No new artifact files", job["error"])

    def test_collection_refuses_a_stale_server_metric_window(self):
        config = asdict(replace(CONFIG, endTime="2026-10-19T10:02:00Z"))
        preview = self.app.preview("collect-evidence", config)
        self.app.start(preview["id"], False)
        job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("timestamps changed", job["error"])
        self.assertEqual(len(job["commands"]), 1)

    def test_terminal_state_is_published_only_after_manifest_is_persisted(self):
        writing = threading.Event()
        release = threading.Event()
        observed = threading.Event()
        original = self.app._persist

        def persist(plan):
            if self.app.jobs[plan.id]["state"] == "succeeded":
                writing.set()
                if not release.wait(5):
                    raise AssertionError("Test did not release the final write.")
            original(plan)

        with patch.object(self.app, "_persist", side_effect=persist):
            preview = self.app.preview("show-run", asdict(CONFIG))
            self.app.start(preview["id"], False)
            self.assertTrue(writing.wait(5))
            observer = threading.Thread(target=lambda: (self.app.job(preview["id"]), observed.set()))
            observer.start()
            try:
                self.assertFalse(observed.wait(.05))
            finally:
                release.set()
                observer.join(5)
            self.assertTrue(observed.is_set())
            self.assertEqual(wait_job(self.app, preview["id"])["state"], "succeeded")

    def test_windows_manifest_lock_retries_publication_not_the_cli(self):
        original = Path.replace
        failures = []

        def replace_locked(path, target):
            if len(failures) < 2:
                failures.append(True)
                error = PermissionError("Temporary Windows sharing lock")
                error.winerror = 32
                raise error
            return original(path, target)

        with patch.object(Path, "replace", new=replace_locked):
            with patch.object(self.app, "runner", side_effect=fake_runner) as runner:
                preview = self.app.preview("show-run", asdict(CONFIG))
                self.app.start(preview["id"], False)
                job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "succeeded", job["error"])
        runner.assert_called_once()
        self.assertEqual(len(failures), 2)

    def test_download_traversal_is_rejected(self):
        for id_ in ("..", r"..\other", "/absolute", "not-a-job"):
            with self.subTest(id=id_), self.assertRaises(ValueError):
                self.app.artifact_directory(id_)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.app = App(Path(cls.temp.name), fake_runner)
        cls.server = DemoServer(cls.app, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.cookie = f"cli_demo={cls.app.token}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.app.close()
        cls.temp.cleanup()

    def request(self, path, body=None, headers=None, authenticated=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        all_headers = {"Cookie": self.cookie} if authenticated else {}
        if body is not None:
            all_headers.update({"Content-Type": "application/json", "X-Demo-Token": self.app.token})
        all_headers.update(headers or {})
        connection.request("POST" if body is not None else "GET", path,
                           json.dumps(body) if body is not None else None, all_headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_public_page_has_security_headers_and_cookie(self):
        status, headers, data = self.request("/", authenticated=False)
        self.assertEqual(status, 200)
        self.assertIn(b"CLI lab", data)
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_api_requires_local_session(self):
        status, _, _ = self.request("/api/bootstrap", authenticated=False)
        self.assertEqual(status, 403)

    def test_landing_page_can_be_opened_from_another_app(self):
        self.assertEqual(self.request("/", headers={"Sec-Fetch-Site": "cross-site",
                                                    "Sec-Fetch-Mode": "navigate"})[0], 200)

    def test_cross_origin_and_rebinding_hosts_are_rejected(self):
        for headers in ({"Origin": "https://evil.invalid"}, {"Host": "evil.invalid"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/bootstrap", headers=headers)[0], 403)

    def test_post_requires_csrf_token(self):
        self.assertEqual(self.request("/api/preview", {"action": "account"},
                                      headers={"X-Demo-Token": ""})[0], 403)

    def test_non_ascii_auth_headers_are_rejected_not_crashed(self):
        self.assertEqual(self.request("/api/bootstrap", headers={"Cookie": 'cli_demo="\\351"'})[0], 403)
        self.assertEqual(self.request("/api/preview", {"action": "account"},
                                      headers={"X-Demo-Token": "\xe9"})[0], 403)

    def test_preview_execute_download_round_trip(self):
        status, _, body = self.request("/api/preview", {"action": "collect-evidence", "config": asdict(CONFIG)})
        self.assertEqual(status, 200, body)
        preview = json.loads(body)
        self.assertEqual(self.request("/api/jobs", {"planId": preview["id"], "confirmed": True})[0], 202)
        job = wait_job(self.app, preview["id"])
        status, _, bundle = self.request(job["bundleUrl"])
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            self.assertIn("client-metrics.csv", archive.namelist())
            self.assertIn("files/logs.zip", archive.namelist())
        status, headers, script = self.request(f"/api/plans/{preview['id']}/script?shell=powershell")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn(b"az load test-run show", script)
        self.assertEqual(self.request(f"/downloads/{preview['id']}/../outside")[0], 400)


if __name__ == "__main__":
    unittest.main()
