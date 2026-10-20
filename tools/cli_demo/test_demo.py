from __future__ import annotations

import csv
import http.client
import io
import json
import shutil
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
    ACTIONS, ROOT, TEST_LIST_COLUMNS, Config, build_commands, csv_export, metric_rows,
    parse_test_list_tsv, redact, render_test_config, run_verdict, script_for, shell_command, tsv_export,
)
from tools.cli_demo.server import App, CliRunner, DemoServer, azure_launcher, main

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
CLIENT_DEFINITIONS = {"value": [
    {"name": "VirtualUsers", "namespace": "LoadTestRunMetrics", "unit": "Count"},
    {"name": "ResponseTime", "namespace": "LoadTestRunMetrics", "unit": "Milliseconds"},
    {"name": "TotalRequests", "namespace": "LoadTestRunMetrics", "unit": "Count"},
    {"name": "Errors", "namespace": "LoadTestRunMetrics", "unit": "Count"},
]}
ALL_CLIENT_METRICS = {
    "VirtualUsers": [{"data": [{"timestamp": CONFIG.startTime, "value": 5}]}],
    "ResponseTime": [{"data": [{"timestamp": CONFIG.startTime, "value": 10}]}],
    "TotalRequests": [{"data": [{"timestamp": CONFIG.startTime, "value": 20}]}],
    "Errors": [{"data": []}],
}
TEST_DEFINITIONS = [
    {"testId": CONFIG.testId, "displayName": 'Smoke "test"', "testType": "JMX",
     "loadTestConfiguration": {"engineInstances": 1}},
    {"testId": "url-test", "displayName": "URL health check", "testType": "URL",
     "loadTestConfiguration": {"engineInstances": 2}},
]


def fake_runner(command):
    if command.artifact == "tests.tsv":
        return subprocess.CompletedProcess(command.args, 0,
                                           'cli-demo-smoke\tSmoke "test"\tJMX\t1\n'
                                           'url-test\tURL health check\tURL\t2\n', "")
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
    elif command.artifact == "client-definitions.json":
        data = CLIENT_DEFINITIONS
    elif command.artifact == "all-client-metrics.json":
        data = ALL_CLIENT_METRICS
    elif command.artifact == "tests.json":
        data = TEST_DEFINITIONS
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

    def test_test_list_output_defaults_to_json_and_only_allows_json_or_tsv(self):
        self.assertEqual(Config.parse({}).testListOutput, "json")
        self.assertEqual(Config.parse({"testListOutput": "tsv"}).testListOutput, "tsv")
        for output in ("yaml", "table", "", "--help"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                Config.parse({"testListOutput": output})
            with self.subTest(output=output), self.assertRaises(ValueError):
                build_commands("list-tests", replace(CONFIG, testListOutput=output), Path("output"))

    def test_list_tests_output_matches_the_preview_and_exported_script(self):
        for output in ("json", "tsv"):
            with self.subTest(output=output):
                commands = build_commands("list-tests", replace(CONFIG, testListOutput=output), Path("output"))
                command = commands[0]
                self.assertEqual(command.args[command.args.index("--output") + 1], output)
                self.assertEqual(command.artifact, f"tests.{output}")
                for shell in ("powershell", "bash"):
                    self.assertIn(f"--output {output}", command.public()[shell])
                    self.assertIn(f"--output {output}", script_for(commands, shell))
                if output == "tsv":
                    self.assertEqual(command.args[command.args.index("--query") + 1],
                                     "[].[testId,displayName,testType,loadTestConfiguration.engineInstances]")
                else:
                    self.assertNotIn("--query", command.args)

    def test_tsv_selection_does_not_change_other_commands_json_output(self):
        config = replace(CONFIG, testListOutput="tsv")
        for action in ACTIONS:
            if action.id == "list-tests":
                continue
            for command in build_commands(action.id, config, Path("output")):
                with self.subTest(action=action.id):
                    self.assertEqual(command.args[command.args.index("--output") + 1], "json")

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

    def test_all_client_metrics_plan_reads_metadata_definitions_and_unfiltered_values(self):
        commands = build_commands("all-client-metrics", CONFIG, Path("output"))
        self.assertEqual([command.artifact for command in commands],
                         ["run.json", "client-definitions.json", "all-client-metrics.json"])
        self.assertNotIn("--metric-name", commands[-1].args)
        self.assertEqual(commands[-1].namespace, "LoadTestRunMetrics")
        self.assertFalse(any(command.download for command in commands))

    def test_small_test_is_fixed_and_target_is_one_argument(self):
        args = build_commands("create-test", CONFIG, Path("output"))[0].args
        self.assertEqual(args[:3], ("load", "test", "create"))
        config_file = Path(args[args.index("--load-test-config-file") + 1])
        self.assertEqual(config_file, Path("output") / "smoke.yaml")
        self.assertEqual(args[args.index("--test-id") + 1], CONFIG.testId)
        self.assertIn("DEMO_HOST=example.invalid", args)
        self.assertIn("DEMO_PROTOCOL=https", args)
        self.assertIn("DEMO_PORT=443", args)
        self.assertIn("DEMO_PATH=/health?a=1&b=2", args)
        self.assertNotIn("--engine-instances", args)

    def test_create_command_uses_checkout_relative_default_inputs(self):
        directory = ROOT / ".loadtest-demo" / "artifacts" / ("a" * 32)
        args = build_commands("create-test", CONFIG, directory)[0].args
        config_path = Path(args[args.index("--load-test-config-file") + 1])
        self.assertEqual(config_path, Path(".loadtest-demo") / "artifacts" / ("a" * 32) / "smoke.yaml")
        self.assertFalse(config_path.is_absolute())
        self.assertNotIn(str(ROOT), shell_command(args))

    def test_create_command_preserves_explicit_external_data_directory(self):
        with tempfile.TemporaryDirectory() as external:
            directory = Path(external) / "artifacts" / ("a" * 32)
            args = build_commands("create-test", CONFIG, directory)[0].args
            self.assertEqual(Path(args[args.index("--load-test-config-file") + 1]), directory / "smoke.yaml")
        self.assertNotIn("--test-plan", args)

    def test_bash_create_preview_uses_shell_appropriate_path_separators(self):
        import shlex
        directory = ROOT / ".loadtest-demo" / "artifacts" / ("a" * 32)
        command = build_commands("create-test", CONFIG, directory)[0]
        tokens = shlex.split(command.public()["bash"])
        self.assertEqual(tokens[tokens.index("--load-test-config-file") + 1],
                         (directory.relative_to(ROOT) / "smoke.yaml").as_posix())

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


class TestConfigTests(unittest.TestCase):
    def test_generated_yaml_uses_the_same_authorized_target_as_cli_arguments(self):
        template = (ROOT / "loadtests" / "cli-demo" / "smoke.yaml").read_text(encoding="utf-8")
        for target in ("https://authorized.example:8443/ready?enabled=true&code=a%3Db",
                       "http://authorized.example", "https://authorized.example/health?filter=a:b"):
            with self.subTest(target=target):
                config = replace(CONFIG, targetUrl=target)
                content = render_test_config(template, config)
                args = build_commands("create-test", config, Path("output"))[0].args
                assignments = args[args.index("--env") + 1:args.index("--subscription")]
                lines = content.splitlines()
                for assignment in assignments:
                    name, expected = assignment.split("=", 1)
                    index = lines.index(f"  - name: {name}")
                    self.assertEqual(json.loads(lines[index + 1].split("value:", 1)[1]), expected)
                self.assertNotIn("example.invalid", content)
                self.assertIn("testPlan: smoke.jmx", content)
                self.assertNotIn("--load-test-config-file .\\loadtests", content)

    def test_generated_yaml_requires_each_target_variable_exactly_once(self):
        template = (ROOT / "loadtests" / "cli-demo" / "smoke.yaml").read_text(encoding="utf-8")
        for name in ("DEMO_HOST", "DEMO_PROTOCOL", "DEMO_PORT", "DEMO_PATH"):
            for content in (template.replace(f"  - name: {name}", f"  - name: OTHER_{name}"),
                            template + f"  - name: {name}\n    value: duplicate\n"):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, f"exactly one.*{name}"):
                    render_test_config(content, CONFIG)


class ExportTests(unittest.TestCase):
    def test_test_list_tsv_keeps_column_order_quotes_unicode_and_zero(self):
        text = 'id\tA "quoted" caf\u00e9\tJMX\t0\r\nempty\t\tURL\t1\r\n'
        table, exported = parse_test_list_tsv(text)
        self.assertEqual(table["headers"], list(TEST_LIST_COLUMNS))
        self.assertEqual(table["rows"], [["id", 'A "quoted" caf\u00e9', "JMX", "0"],
                                         ["empty", "", "URL", "1"]])
        self.assertEqual(list(csv.reader(io.StringIO(exported), delimiter="\t")),
                         [list(TEST_LIST_COLUMNS), *table["rows"]])

    def test_test_list_tsv_has_a_header_even_when_no_definitions_exist(self):
        table, exported = parse_test_list_tsv("")
        self.assertEqual(table["rows"], [])
        self.assertEqual(list(csv.reader(io.StringIO(exported), delimiter="\t")), [list(TEST_LIST_COLUMNS)])

    def test_test_list_tsv_rejects_misaligned_rows(self):
        for text in ("id\tName\tJMX\n", "id\tName\twith tab\tJMX\t1\n", "id\tName\nnext\tJMX\t1\n"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "unexpected TSV columns"):
                parse_test_list_tsv(text)
        with self.assertRaisesRegex(ValueError, "Could not read.*TSV"):
            parse_test_list_tsv("id\t" + "x" * 140000 + "\tJMX\t1\n")

    def test_test_list_tsv_neutralizes_spreadsheet_formulas_without_altering_the_table(self):
        table, exported = parse_test_list_tsv("id\t=HYPERLINK()\tJMX\t1\n")
        self.assertEqual(table["rows"][0][1], "=HYPERLINK()")
        self.assertEqual(list(csv.reader(io.StringIO(exported), delimiter="\t"))[1][1], "'=HYPERLINK()")

    def test_tsv_has_the_same_columns_values_and_units_as_csv(self):
        kwargs = {"namespace": "LoadTestRunMetrics", "definitions": CLIENT_DEFINITIONS}
        csv_rows = list(csv.DictReader(io.StringIO(csv_export(ALL_CLIENT_METRICS, **kwargs))))
        tsv_reader = csv.DictReader(io.StringIO(tsv_export(ALL_CLIENT_METRICS, **kwargs)), delimiter="\t")
        self.assertEqual(tsv_reader.fieldnames,
                         ["timestamp", "namespace", "metric", "unit", "dimensions", "aggregation", "value"])
        self.assertEqual(list(tsv_reader), csv_rows)
        self.assertFalse(any(row["metric"] == "Errors" for row in csv_rows))

    def test_tsv_preserves_zero_and_omits_missing_values(self):
        data = {"Errors": [{"data": [{"timestamp": CONFIG.startTime, "value": 0},
                                    {"timestamp": CONFIG.endTime, "value": None}]}]}
        rows = list(csv.DictReader(io.StringIO(tsv_export(
            data, namespace="LoadTestRunMetrics", definitions=CLIENT_DEFINITIONS)), delimiter="\t"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], "0")
        self.assertEqual(rows[0]["unit"], "Count")

    def test_tsv_quotes_embedded_tabs_newlines_and_quotes(self):
        name = 'Request\t"quoted"\nnext line'
        data = [{"dimensions": {"RequestName": 'health\t"quoted"\nnext'},
                 "data": [{"timestamp": CONFIG.startTime, "value": 1}]}]
        rows = list(csv.DictReader(io.StringIO(tsv_export(data, name)), delimiter="\t"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metric"], name)
        self.assertEqual(json.loads(rows[0]["dimensions"])["RequestName"], 'health\t"quoted"\nnext')

    def test_tsv_neutralizes_spreadsheet_formulas(self):
        data = [{"data": [{"timestamp": CONFIG.startTime, "value": -2}]}]
        for name in ("=HYPERLINK()", "+SUM(A1)", "-command", "@SUM(A1)", "\tleading", "\rleading"):
            with self.subTest(name=name):
                rows = list(csv.DictReader(io.StringIO(tsv_export(data, name)), delimiter="\t"))
                self.assertEqual(rows[0]["metric"], "'" + name)
                self.assertEqual(rows[0]["value"], "-2")

    def test_empty_tsv_keeps_a_header_without_invented_data(self):
        reader = csv.DictReader(io.StringIO(tsv_export({"Errors": [{"data": []}]},
                                                      namespace="LoadTestRunMetrics")), delimiter="\t")
        self.assertIn("metric", reader.fieldnames)
        self.assertEqual(list(reader), [])

    def test_tsv_preserves_server_aggregation_and_units(self):
        data = {"value": [{"name": {"value": "CpuTime"}, "unit": "Seconds",
                           "timeseries": [{"data": [{"timeStamp": CONFIG.startTime, "total": 0}]}]}]}
        rows = list(csv.DictReader(io.StringIO(tsv_export(data)), delimiter="\t"))
        self.assertEqual(rows[0]["metric"], "CpuTime")
        self.assertEqual(rows[0]["unit"], "Seconds")
        self.assertEqual(rows[0]["aggregation"], "total")
        self.assertEqual(rows[0]["value"], "0")

    def test_all_client_metric_map_retains_names_and_units(self):
        rows = metric_rows(ALL_CLIENT_METRICS, namespace="LoadTestRunMetrics", definitions=CLIENT_DEFINITIONS)
        self.assertEqual({row["metric"] for row in rows}, {"VirtualUsers", "ResponseTime", "TotalRequests"})
        response = next(row for row in rows if row["metric"] == "ResponseTime")
        self.assertEqual(response["unit"], "Milliseconds")
        self.assertEqual(response["namespace"], "LoadTestRunMetrics")
        self.assertFalse(any(row["metric"] == "Errors" for row in rows))

    def test_all_client_metric_csv_exports_every_populated_series(self):
        rows = list(csv.DictReader(io.StringIO(csv_export(
            ALL_CLIENT_METRICS, namespace="LoadTestRunMetrics", definitions=CLIENT_DEFINITIONS))))
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["metric"] for row in rows}, {"VirtualUsers", "ResponseTime", "TotalRequests"})
        self.assertEqual(next(row for row in rows if row["metric"] == "ResponseTime")["unit"], "Milliseconds")

    def test_all_client_metric_export_retains_zero_and_dimensions(self):
        data = {"Errors": [{"dimensions": {"RequestName": "HealthCheck"},
                            "data": [{"timestamp": CONFIG.startTime, "value": 0},
                                     {"timestamp": CONFIG.endTime, "value": None}]}]}
        rows = metric_rows(data, namespace="LoadTestRunMetrics", definitions=CLIENT_DEFINITIONS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 0)
        self.assertEqual(rows[0]["unit"], "Count")
        self.assertIn("HealthCheck", rows[0]["dimensions"])

    def test_all_client_metric_names_work_without_definition_metadata(self):
        rows = metric_rows({"NewMetric": [{"data": [{"timestamp": CONFIG.startTime, "value": 7}]}]},
                           namespace="LoadTestRunMetrics")
        self.assertEqual(rows[0]["metric"], "NewMetric")
        self.assertEqual(rows[0]["unit"], "")

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


class PortableScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.checkout = Path(self.temp.name) / "customer-checkout"
        sources = self.checkout / "loadtests" / "cli-demo"
        sources.mkdir(parents=True)
        for name in ("smoke.yaml", "smoke.jmx"):
            shutil.copyfile(ROOT / "loadtests" / "cli-demo" / name, sources / name)
        self.original_directory = Path(self.temp.name) / "copilot-session" / "artifacts" / ("a" * 32)
        self.config = replace(CONFIG, testId="portable-customer-test",
                              targetUrl="https://authorized.example:8443/ready?a=1&b=2")
        self.commands = build_commands("create-test", self.config, self.original_directory)
        content = render_test_config((sources / "smoke.yaml").read_text(encoding="utf-8"), self.config)
        self.test_config = {"path": str(self.original_directory / "smoke.yaml"), "content": content}

    def tearDown(self):
        self.temp.cleanup()

    def test_exports_prepare_matching_yaml_and_jmx_without_session_paths(self):
        for shell in ("powershell", "bash"):
            with self.subTest(shell=shell):
                script = script_for(self.commands, shell, self.test_config)
                self.assertNotIn(str(self.original_directory), script)
                self.assertNotIn(str(ROOT), script)
                self.assertIn('testId: "portable-customer-test"', script)
                self.assertIn("--test-id portable-customer-test", script)
                self.assertIn('value: "authorized.example"', script)
                self.assertIn('value: "8443"', script)
                self.assertIn('value: "/ready?a=1&b=2"', script)
                self.assertIn("DEMO_HOST=authorized.example", script)
                self.assertIn("Run this script from the repository root", script)
                self.assertIn("smoke.jmx", script)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required to execute its exported script.")
    def test_powershell_create_export_runs_from_a_clean_checkout(self):
        script = script_for(self.commands, "powershell", self.test_config)
        path = self.checkout / "create.ps1"
        path.write_text(script, encoding="utf-8", newline="")
        wrapper = (
            "Set-Location -LiteralPath '" + str(self.checkout).replace("'", "''") + "'; "
            "function az { $global:LASTEXITCODE = 0; "
            "[System.IO.File]::WriteAllText((Join-Path $PWD.Path 'argv.json'), "
            "(ConvertTo-Json -InputObject @($args) -Compress)) }; "
            "& '.\\create.ps1'"
        )
        result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", wrapper],
                                cwd=self.checkout.parent, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads((self.checkout / "argv.json").read_text(encoding="utf-8"))
        config_file = Path(args[args.index("--load-test-config-file") + 1])
        self.assertFalse(config_file.is_absolute())
        generated = self.checkout / config_file
        self.assertEqual(generated.read_text(encoding="utf-8"), self.test_config["content"])
        self.assertEqual(generated.with_suffix(".jmx").read_bytes(),
                         (ROOT / "loadtests" / "cli-demo" / "smoke.jmx").read_bytes())
        self.assertFalse(self.original_directory.exists())

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required to execute its exported script.")
    def test_create_export_refuses_to_run_outside_a_checkout(self):
        wrong_directory = Path(self.temp.name) / "not-a-checkout"
        wrong_directory.mkdir()
        path = wrong_directory / "create.ps1"
        path.write_text(script_for(self.commands, "powershell", self.test_config), encoding="utf-8", newline="")
        result = subprocess.run(
            [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command",
             "function az { throw 'Azure CLI must not run here.' }; & '.\\create.ps1'"],
            cwd=wrong_directory, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Run this script from the repository root", result.stderr)
        self.assertFalse((wrong_directory / ".loadtest-demo").exists())


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = App(Path(self.temp.name), fake_runner)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_preview_is_not_execution_and_requires_confirmation(self):
        preview = self.app.preview("run-test", asdict(CONFIG))
        self.assertIsNone(preview["testConfig"])
        self.assertFalse(Path(preview["artifactDirectory"]).exists())
        with self.assertRaisesRegex(ValueError, "Confirm"):
            self.app.start(preview["id"], False)

    def test_create_previews_freeze_ui_test_ids_without_editing_template(self):
        template = ROOT / "loadtests" / "cli-demo" / "smoke.yaml"
        original = template.read_bytes()
        previews = []
        with patch.object(self.app, "runner", side_effect=fake_runner) as runner:
            for test_id in ("cli-demo-smoke-1", "cli-demo-smoke-2", "123", "true"):
                preview = self.app.preview("create-test", asdict(replace(CONFIG, testId=test_id)))
                previews.append(preview)
                args = preview["commands"][0]["argv"]
                self.assertEqual(args[args.index("--test-id") + 1], test_id)
                test_id_line = next(line for line in preview["testConfig"]["content"].splitlines()
                                    if line.startswith("testId:"))
                self.assertEqual(json.loads(test_id_line.removeprefix("testId:").strip()), test_id)
            runner.assert_not_called()
            self.assertEqual(len({preview["testConfig"]["path"] for preview in previews}), len(previews))
            first = previews[0]
            self.app.start(first["id"], True)
            job = wait_job(self.app, first["id"])
            self.assertEqual(job["state"], "succeeded", job["error"])
            runner.assert_called_once()
            command = runner.call_args.args[0]
            config_file = Path(command.args[command.args.index("--load-test-config-file") + 1])
            self.assertEqual(config_file.read_text(encoding="utf-8"), first["testConfig"]["content"])
            self.assertIn('testId: "cli-demo-smoke-1"', first["testConfig"]["content"])
            self.assertTrue({"smoke.yaml", "smoke.jmx", "manifest.json", "test.json"}.issubset(
                {file["name"] for file in job["files"]}))
        self.assertEqual(template.read_bytes(), original)

    def test_create_preview_requires_exactly_one_template_test_id(self):
        for content in ("version: v0.1\n", "testId: first\ntestId: second\n"):
            with self.subTest(content=content):
                with patch("tools.cli_demo.server.Path.read_text", return_value=content):
                    with self.assertRaisesRegex(ValueError, "exactly one.*testId"):
                        self.app.preview("create-test", asdict(CONFIG))
        self.assertEqual(self.app.jobs, {})

    def test_create_previews_freeze_authorized_targets_in_the_generated_yaml(self):
        template = ROOT / "loadtests" / "cli-demo" / "smoke.yaml"
        original = template.read_bytes()
        first = self.app.preview("create-test", asdict(replace(
            CONFIG, targetUrl="https://first.authorized.example:8443/ready?q=a&b=2")))
        second = self.app.preview("create-test", asdict(replace(
            CONFIG, targetUrl="http://second.authorized.example/status")))
        self.assertNotEqual(first["testConfig"]["path"], second["testConfig"]["path"])
        for preview, host, protocol, port, path in (
            (first, "first.authorized.example", "https", "8443", "/ready?q=a&b=2"),
            (second, "second.authorized.example", "http", "80", "/status"),
        ):
            content = preview["testConfig"]["content"]
            self.assertEqual(Path(preview["testConfig"]["path"]).read_text(encoding="utf-8"), content)
            self.assertEqual(preview["testConfig"]["sourcePath"], str(template.relative_to(ROOT)))
            args = preview["commands"][0]["argv"]
            for name, value in (("DEMO_HOST", host), ("DEMO_PROTOCOL", protocol),
                                ("DEMO_PORT", port), ("DEMO_PATH", path)):
                self.assertIn(f"{name}={value}", args)
                self.assertIn(f"  - name: {name}\n    value: {json.dumps(value)}", content)
        self.assertEqual(template.read_bytes(), original)

    def test_default_create_preview_writes_relative_inputs_in_the_checkout(self):
        with tempfile.TemporaryDirectory() as root:
            checkout = Path(root).resolve()
            sources = checkout / "loadtests" / "cli-demo"
            sources.mkdir(parents=True)
            for name in ("smoke.yaml", "smoke.jmx"):
                shutil.copyfile(ROOT / "loadtests" / "cli-demo" / name, sources / name)
            app = App(checkout / ".loadtest-demo", fake_runner)
            try:
                with patch("tools.cli_demo.commands.ROOT", checkout), patch("tools.cli_demo.server.ROOT", checkout):
                    preview = app.preview("create-test", asdict(CONFIG))
                args = preview["commands"][0]["argv"]
                config_path = Path(args[args.index("--load-test-config-file") + 1])
                self.assertFalse(config_path.is_absolute())
                self.assertEqual(preview["testConfig"]["path"], str(config_path))
                self.assertEqual(preview["testConfig"]["sourcePath"], str(Path("loadtests") / "cli-demo" / "smoke.yaml"))
                self.assertEqual((checkout / config_path).read_text(encoding="utf-8"),
                                 preview["testConfig"]["content"])
                self.assertTrue((checkout / config_path.with_suffix(".jmx")).is_file())
            finally:
                app.close()

    def test_json_test_list_job_retains_structured_data_for_the_json_viewer(self):
        preview = self.app.preview("list-tests", asdict(CONFIG))
        self.app.start(preview["id"], False)
        job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "succeeded", job["error"])
        entry = job["commands"][0]
        self.assertEqual(entry["format"], "json")
        self.assertEqual(entry["data"], TEST_DEFINITIONS)
        self.assertEqual(json.loads(entry["stdout"]), TEST_DEFINITIONS)

    def test_tsv_test_list_job_persists_a_headered_download_and_readable_columns(self):
        preview = self.app.preview("list-tests", asdict(replace(CONFIG, testListOutput="tsv")))
        self.app.start(preview["id"], False)
        job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "succeeded", job["error"])
        entry = job["commands"][0]
        self.assertEqual(entry["table"]["headers"], list(TEST_LIST_COLUMNS))
        self.assertEqual(entry["table"]["rows"][0], ["cli-demo-smoke", 'Smoke "test"', "JMX", "1"])
        content = (Path(job["artifactDirectory"]) / "tests.tsv").read_bytes()
        self.assertNotIn(b"\r\r\n", content)
        self.assertEqual(list(csv.reader(io.StringIO(content.decode("utf-8")), delimiter="\t")),
                         [list(TEST_LIST_COLUMNS), *entry["table"]["rows"]])
        reopened = App(Path(self.temp.name), fake_runner)
        try:
            self.assertEqual(reopened.job(job["id"])["commands"][0]["table"], entry["table"])
        finally:
            reopened.close()

    def test_malformed_tsv_fails_explicitly_and_keeps_raw_stdout(self):
        with patch.object(self.app, "runner", return_value=subprocess.CompletedProcess(
            [], 0, "id\ttoo\tmany\tcolumns\tJMX\t1\n", "")):
            preview = self.app.preview("list-tests", asdict(replace(CONFIG, testListOutput="tsv")))
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("unexpected TSV columns", job["error"])
        self.assertIn("too\tmany", job["commands"][0]["stdout"])
        self.assertNotIn("table", job["commands"][0])

    def test_invalid_json_does_not_publish_a_success_shaped_json_preview(self):
        with patch.object(self.app, "runner", return_value=subprocess.CompletedProcess([], 0, "not JSON", "")):
            preview = self.app.preview("list-tests", asdict(CONFIG))
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("non-JSON", job["error"])
        self.assertNotIn("format", job["commands"][0])

    def test_all_client_metric_job_persists_raw_empty_series_and_labeled_exports(self):
        preview = self.app.preview("all-client-metrics", asdict(CONFIG))
        self.app.start(preview["id"], False)
        job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "succeeded", job["error"])
        entry = next(item for item in job["commands"] if item["artifact"] == "all-client-metrics.json")
        self.assertEqual(entry["data"]["Errors"], [{"data": []}])
        self.assertEqual(len(entry["metricRows"]), 3)
        self.assertEqual({row["unit"] for row in entry["metricRows"]}, {"Count", "Milliseconds"})
        names = {file["name"] for file in job["files"]}
        self.assertTrue({"run.json", "client-definitions.json", "all-client-metrics.json",
                         "all-client-metrics.csv", "all-client-metrics.tsv"}.issubset(names))

    def test_tsv_file_bytes_do_not_gain_extra_windows_line_endings(self):
        preview = self.app.preview("client-metrics", asdict(CONFIG))
        self.app.start(preview["id"], False)
        job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "succeeded", job["error"])
        data = job["commands"][0]["data"]
        path = Path(job["artifactDirectory"]) / "client-metrics.tsv"
        self.assertEqual(path.read_bytes(),
                         tsv_export(data, CONFIG.clientMetric, "LoadTestRunMetrics").encode("utf-8"))
        self.assertNotIn(b"\r\r\n", path.read_bytes())

    def test_all_client_metric_job_rejects_a_run_from_another_test(self):
        preview = self.app.preview("all-client-metrics", asdict(replace(CONFIG, testId="another-test")))
        self.app.start(preview["id"], False)
        job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("does not belong", job["error"])
        self.assertEqual(len(job["commands"]), 1)

    def test_all_client_metric_job_warns_when_no_points_are_available(self):
        def empty_metrics(command):
            if command.artifact == "all-client-metrics.json":
                return subprocess.CompletedProcess(command.args, 0, "{}", "")
            return fake_runner(command)

        with patch.object(self.app, "runner", side_effect=empty_metrics):
            preview = self.app.preview("all-client-metrics", asdict(CONFIG))
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "succeeded", job["error"])
        self.assertTrue(job["warnings"])
        self.assertEqual(job["commands"][-1]["metricRows"], [])

    def test_all_client_metric_request_failure_is_explicit(self):
        def failed_metrics(command):
            if command.artifact == "all-client-metrics.json":
                return subprocess.CompletedProcess(command.args, 3, "", "Metric query denied")
            return fake_runner(command)

        with patch.object(self.app, "runner", side_effect=failed_metrics):
            preview = self.app.preview("all-client-metrics", asdict(CONFIG))
            self.app.start(preview["id"], False)
            job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("Metric query denied", job["commands"][-1]["stderr"])

    def test_preview_runs_once_and_persists_artifacts(self):
        preview = self.app.preview("collect-evidence", asdict(CONFIG))
        self.app.start(preview["id"], True)
        job = wait_job(self.app, preview["id"])
        self.assertEqual(job["state"], "succeeded", job["error"])
        names = {file["name"] for file in job["files"]}
        self.assertTrue({"run.json", "client-metrics.csv", "engine-metrics.json",
                         "server-metrics.csv", "files/logs.zip", "files/results.zip",
                         "manifest.json"}.issubset(names))
        for metric in ("client-metrics", "engine-metrics", "server-metrics"):
            self.assertIn(metric + ".tsv", names)
            directory = Path(job["artifactDirectory"])
            csv_rows = list(csv.DictReader(io.StringIO((directory / (metric + ".csv")).read_text(encoding="utf-8"))))
            tsv_rows = list(csv.DictReader(io.StringIO((directory / (metric + ".tsv")).read_text(encoding="utf-8")),
                                          delimiter="\t"))
            self.assertEqual(tsv_rows, csv_rows)
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


class StartupTests(unittest.TestCase):
    def test_server_default_port(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(Path(directory), fake_runner)
            self.addCleanup(app.close)
            with patch("tools.cli_demo.server.ThreadingHTTPServer.__init__", return_value=None) as initialize:
                DemoServer(app)
            self.assertEqual(initialize.call_args.args[0], ("127.0.0.1", 8769))

    def test_cli_default_and_custom_ports(self):
        for args, port in (([], 8769), (["--port", "8766"], 8766)):
            with self.subTest(port=port), tempfile.TemporaryDirectory() as directory:
                with (
                    patch("sys.argv", ["cli-demo", "--data-dir", directory, *args]),
                    patch("tools.cli_demo.server.DemoServer") as server,
                    patch("tools.cli_demo.server.logging.basicConfig"),
                    patch("builtins.print") as output,
                ):
                    main()
                self.assertEqual(server.call_args.args[1], port)
                output.assert_any_call(f"Azure Load Testing CLI demo: http://127.0.0.1:{port}", flush=True)


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
        self.assertIn(b'href="https://learn.microsoft.com/en-us/azure/app-testing/load-testing/reference-test-config-yaml"', data)
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn(b'id="test-list-output" data-config="testListOutput"', data)
        self.assertIn(b'<option value="tsv">TSV - readable columns</option>', data)
        self.assertIn(b'<div id="output"', data)
        self.assertIn(b'id="yaml-source-path"', data)

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

    def test_create_preview_shows_yaml_used_by_both_step_scripts(self):
        test_id = "cli-demo-smoke-1"
        with patch.object(self.app, "runner") as runner:
            status, _, body = self.request("/api/preview", {
                "action": "create-test", "config": asdict(replace(CONFIG, testId=test_id)),
            })
        self.assertEqual(status, 200, body)
        runner.assert_not_called()
        preview = json.loads(body)
        args = preview["commands"][0]["argv"]
        config_file = Path(args[args.index("--load-test-config-file") + 1])
        self.assertEqual(config_file, Path(preview["artifactDirectory"]) / "smoke.yaml")
        self.assertEqual(preview["testConfig"], {
            "path": str(config_file), "sourcePath": str(Path("loadtests") / "cli-demo" / "smoke.yaml"),
            "content": config_file.read_text(encoding="utf-8"),
        })
        self.assertIn(f'testId: "{test_id}"', preview["testConfig"]["content"])
        self.assertIn("testPlan: smoke.jmx", preview["testConfig"]["content"])
        self.assertEqual(config_file.with_suffix(".jmx").read_bytes(),
                         (ROOT / "loadtests" / "cli-demo" / "smoke.jmx").read_bytes())
        self.assertFalse(config_file.with_name("manifest.json").exists())
        self.assertNotIn(preview["id"], self.app.jobs)
        self.assertNotIn(preview["id"], {job["id"] for job in self.app.saved_artifacts()})
        for shell in ("powershell", "bash"):
            with self.subTest(shell=shell):
                status, _, script = self.request(f"/api/plans/{preview['id']}/script?shell={shell}")
                self.assertEqual(status, 200)
                self.assertIn(f"--test-id {test_id}".encode("utf-8"), script)
                self.assertIn(preview["testConfig"]["content"].strip().encode("utf-8"), script)
                self.assertNotIn(preview["artifactDirectory"].encode("utf-8"), script)
                self.assertIn(b"smoke.jmx", script)

    def test_create_preview_reports_unreadable_yaml(self):
        with patch("tools.cli_demo.server.Path.read_text", side_effect=FileNotFoundError("smoke.yaml")):
            status, _, body = self.request("/api/preview", {"action": "create-test", "config": asdict(CONFIG)})
        self.assertEqual(status, 400, body)
        self.assertIn("Could not read the demo YAML", json.loads(body)["error"])

    def test_create_preview_reports_unwritable_generated_yaml(self):
        with patch("tools.cli_demo.server.Path.write_text", side_effect=PermissionError("Read-only directory")):
            status, _, body = self.request("/api/preview", {"action": "create-test", "config": asdict(CONFIG)})
        self.assertEqual(status, 400, body)
        self.assertIn("Could not prepare the demo YAML", json.loads(body)["error"])

    def test_list_test_tsv_preview_execution_and_download_match_the_selected_format(self):
        status, _, body = self.request("/api/preview", {
            "action": "list-tests", "config": asdict(replace(CONFIG, testListOutput="tsv")),
        })
        self.assertEqual(status, 200, body)
        preview = json.loads(body)
        args = preview["commands"][0]["argv"]
        self.assertEqual(args[args.index("--output") + 1], "tsv")
        self.assertEqual(self.request("/api/jobs", {"planId": preview["id"]})[0], 202)
        job = wait_job(self.app, preview["id"])
        file = next(item for item in job["files"] if item["name"] == "tests.tsv")
        status, headers, body = self.request(file["url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/tab-separated-values; charset=utf-8")
        rows = list(csv.reader(io.StringIO(body.decode("utf-8")), delimiter="\t"))
        self.assertEqual(rows, [list(TEST_LIST_COLUMNS), *job["commands"][0]["table"]["rows"]])

    def test_invalid_test_list_output_is_rejected(self):
        status, _, body = self.request("/api/preview", {
            "action": "list-tests", "config": asdict(replace(CONFIG, testListOutput="table")),
        })
        self.assertEqual(status, 400, body)
        self.assertIn("Choose json or tsv", json.loads(body)["error"])

    def test_tsv_download_is_a_headered_utf8_attachment(self):
        status, _, body = self.request("/api/preview", {"action": "all-client-metrics", "config": asdict(CONFIG)})
        self.assertEqual(status, 200)
        preview = json.loads(body)
        self.assertEqual(self.request("/api/jobs", {"planId": preview["id"]})[0], 202)
        job = wait_job(self.app, preview["id"])
        file = next(item for item in job["files"] if item["name"] == "all-client-metrics.tsv")
        status, headers, body = self.request(file["url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/tab-separated-values; charset=utf-8")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertTrue(body.startswith(b"timestamp\tnamespace\tmetric\tunit\t"))
        self.assertEqual(len(list(csv.DictReader(io.StringIO(body.decode("utf-8")), delimiter="\t"))), 3)

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
            self.assertIn("client-metrics.tsv", archive.namelist())
            self.assertIn("engine-metrics.tsv", archive.namelist())
            self.assertIn("server-metrics.tsv", archive.namelist())
            self.assertIn("files/logs.zip", archive.namelist())
        status, headers, script = self.request(f"/api/plans/{preview['id']}/script?shell=powershell")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn(b"az load test-run show", script)
        self.assertEqual(self.request(f"/downloads/{preview['id']}/../outside")[0], 400)


if __name__ == "__main__":
    unittest.main()
