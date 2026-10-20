import hashlib
import csv
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.gitlab_demo.common import DemoError, ROOT, read_json, write_json
from tools.gitlab_demo.azure_http import AzureAPIError
from tools.sql_metrics_demo import __main__ as demo
from tools.sql_metrics_demo.client import trusted_artifact_url
from tools.sql_metrics_demo.contract import (
    INFRA, JDBC_NAME, LOAD, METRICS, NAMESPACE, Context, check_plan,
    metric_configuration_patch, selected_metrics, state_directory, test_configuration,
)
from tools.sql_metrics_demo.evidence import TelemetryPending, database_evidence, metric_rows, verify, write_csv


def context():
    subscription = "11111111-1111-1111-1111-111111111111"
    return Context(
        subscription, "22222222-2222-2222-2222-222222222222", "unit-demo", "swedencentral",
        f"/subscriptions/{subscription}/resourceGroups/existing/providers/Microsoft.LoadTestService/loadTests/existing",
        "33333333-3333-3333-3333-333333333333", "44444444-4444-4444-4444-444444444444",
        "66666666-6666-6666-6666-666666666666",
    )


def evidence():
    ctx = context()
    criteria = test_configuration(ctx)["passFailCriteria"]
    for name, item in criteria["passFailMetrics"].items():
        item.update(result="passed", actualValue={"no-errors": 0, "real-sql-work": 100, "bounded-latency": 12}[name])
    run = {
        "testRunId": "sql-metrics-fixture", "testId": ctx.test_id, "status": "DONE",
        "testResult": "PASSED", "passFailCriteria": criteria,
        "loadTestConfiguration": {"engineInstances": 1},
        "startDateTime": "2026-10-20T12:00:05Z", "endDateTime": "2026-10-20T12:02:05Z",
        "testRunStatistics": {
            "Total": {"sampleCount": 102, "pct2ResTime": 12, "errorPct": 0},
            "SQLTransaction": {"sampleCount": 100, "errorCount": 0, "pct2ResTime": 12},
        },
    }
    token = "55555555-5555-5555-5555-555555555555"
    database = {
        "run_token": token, "completed_operations": 100, "account_count": 512, "balance_delta": 100,
        "successful_request_samples": 100,
        "workload_start_utc": "2026-10-20T12:00:15+00:00", "workload_end_utc": "2026-10-20T12:02:00+00:00",
    }
    metrics = []
    for name, aggregation in METRICS.items():
        value = 0 if name in {"physical_data_read_percent", "connection_successful"} else 1
        metrics.append({
            "id": f"{ctx.database_id}/providers/Microsoft.Insights/metrics/{name}",
            "name": {"value": name},
            "timeseries": [{"data": [
                {"timeStamp": "2026-10-20T11:59:00Z", aggregation.lower(): 99},
                {"timeStamp": "2026-10-20T12:01:00Z", aggregation.lower(): value},
                {"timeStamp": "2026-10-20T12:02:00Z", aggregation.lower(): 99},
            ]}],
        })
    return ctx, run, database, [{"interval": "PT1M", "value": metrics}], token


class SqlMetricsContractTests(unittest.TestCase):
    def test_only_standard_or_dns_zone_azure_blob_artifact_origins_are_allowed(self):
        for host in ("demoaccount.blob.core.windows.net", "demoaccount.z17.blob.storage.azure.net"):
            uri = f"https://{host}/result.zip?sig=fixture"
            self.assertEqual(trusted_artifact_url(uri), uri)
        for uri in (
            "http://demoaccount.blob.core.windows.net/result.zip",
            "https://demoaccount.z17.blob.storage.azure.net.attacker.invalid/result.zip",
            "https://user:password@demoaccount.blob.core.windows.net/result.zip",
            "https://demoaccount.blob.core.windows.net:8080/result.zip",
            "https://example.invalid/result.zip",
            "https://demoaccount.z99.blob.storage.azure.net/result.zip",
        ):
            with self.subTest(uri=uri), self.assertRaises(DemoError):
                trusted_artifact_url(uri)

    def test_scope_is_deterministic_separate_and_not_user_selectable(self):
        ctx = context()
        self.assertNotIn("/resourceGroups/existing/", ctx.database_id)
        self.assertIn(ctx.group_name, ctx.database_id)
        self.assertNotEqual(ctx.token, replace(ctx, demo_name="another-demo").token)
        expected = hashlib.sha256(
            f"{ctx.subscription_id}/{ctx.load_test_resource_id.lower()}/{ctx.demo_name}".encode()
        ).hexdigest()[:10]
        self.assertEqual(ctx.token, expected)
        self.assertLessEqual(len(ctx.test_id), 50)
        with self.assertRaises(DemoError):
            replace(ctx, subscription_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        for name in ("../demo", "a", "UPPER", "x" * 21):
            with self.subTest(name=name), self.assertRaises(DemoError):
                replace(ctx, demo_name=name)

    def test_state_must_be_inside_named_ignored_folder(self):
        allowed = ROOT / ".loadtest-demo" / "sql-metrics" / "fixture"
        self.assertEqual(state_directory(allowed), allowed.resolve())
        for folder in (ROOT, ROOT / ".loadtest-demo" / "sql-metrics", ROOT / "infra", allowed / ".." / ".." / ".."):
            with self.subTest(folder=folder), self.assertRaises(DemoError):
                state_directory(folder)

    def test_new_test_is_not_in_auto_provisioned_catalog(self):
        self.assertNotEqual(LOAD.parent, ROOT / "loadtests" / "config")
        self.assertFalse(LOAD.is_relative_to(ROOT / "loadtests" / "config"))
        payload = test_configuration(context())
        self.assertEqual(payload["loadTestConfiguration"]["engineInstances"], 1)
        self.assertEqual(payload["metricsReferenceIdentityType"], "SystemAssigned")
        self.assertEqual(payload["engineBuiltInIdentityType"], "SystemAssigned")
        self.assertEqual(payload["subnetId"], context().subnet_id)
        self.assertFalse(payload["publicIPDisabled"])
        self.assertNotIn("secrets", payload)
        self.assertEqual(payload["autoStopCriteria"]["maximumVirtualUsersPerEngine"], 2)

    def test_jmeter_has_two_bounds_one_second_pacing_and_no_secret_logging(self):
        root = ET.parse(LOAD / "sql-metrics.jmx").getroot()
        group = root.find(".//ThreadGroup")
        values = {element.attrib.get("name"): element.text for element in group.iter()}
        self.assertEqual(values["ThreadGroup.num_threads"], "2")
        self.assertEqual(values["ThreadGroup.duration"], "120")
        self.assertEqual(values["LoopController.loops"], "120")
        self.assertEqual(values["ThreadGroup.scheduler"], "true")
        self.assertEqual(root.find(".//ConstantTimer/stringProp").text, "1000")
        self.assertEqual(root.find(".//SetupThreadGroup/stringProp").text, "stoptest")
        for tree in root.iter("hashTree"):
            children = list(tree)
            self.assertEqual(len(children) % 2, 0)
            for index in range(1, len(children), 2):
                self.assertEqual(children[index].tag, "hashTree")
        properties = (LOAD / "user.properties").read_text()
        self.assertIn("response_data.on_error=false", properties)
        self.assertIn("samplerData=false", properties)
        self.assertIn("log_level.com.microsoft.sqlserver.jdbc=OFF", properties)
        transaction = (LOAD / "transaction.groovy").read_text()
        self.assertIn("prepareCall('{call dbo.DemoTransaction(?, ?)}')", transaction)
        self.assertIn("setQueryTimeout(10)", transaction)
        self.assertIn("trustServerCertificate=false", transaction)
        self.assertNotIn("error.message", transaction)

    def test_schema_bounds_and_minimal_data_permissions(self):
        sql = (INFRA / "schema.sql").read_text()
        self.assertIn("completed_operations BETWEEN 0 AND 240", sql)
        self.assertIn("account_id BETWEEN 1 AND 512", sql)
        self.assertIn("TYPE = E", sql)
        self.assertIn("GRANT EXECUTE ON OBJECT::dbo.DemoTransaction", sql)
        self.assertNotIn("db_owner", sql)
        self.assertNotIn("GRANT SELECT", sql)
        infrastructure = (INFRA / "main.tf").read_text()
        self.assertIn('sku_name             = "Basic"', infrastructure)
        self.assertIn("azuread_authentication_only = true", infrastructure)
        self.assertIn("public_network_access_enabled = false", infrastructure)
        self.assertIn('resource "azurerm_private_endpoint" "sql"', infrastructure)
        self.assertIn('resource "azurerm_private_dns_zone" "sql"', infrastructure)
        self.assertNotIn("administrator_login_password", infrastructure)
        self.assertNotIn('resource "azurerm_mssql_virtual_network_rule"', infrastructure)
        self.assertNotIn('resource "azurerm_load_test"', infrastructure)
        self.assertIn('scope                            = azurerm_mssql_database.demo.id', infrastructure)

    def test_plan_allows_only_new_demo_resources_and_targeted_destroy(self):
        ctx = context()
        def plan(address, actions, before=None):
            return {"resource_changes": [{"address": address, "mode": "managed", "change": {"actions": actions, "before": before}}]}
        check_plan(plan("azurerm_mssql_database.demo", ["create"]), ctx)
        check_plan(plan("azurerm_mssql_database.demo", ["delete"], {"id": ctx.database_id}), ctx, destroy=True)
        for item, destroy in (
            (plan("azurerm_load_test.existing", ["delete"], {"id": ctx.load_test_resource_id}), True),
            (plan("azurerm_mssql_database.demo", ["delete"], {"id": ctx.database_id}), False),
            (plan("azurerm_mssql_database.demo", ["update"]), False),
            (plan("azurerm_mssql_database.demo", ["delete", "create"]), False),
            (plan("azurerm_mssql_database.demo", ["delete"], {"id": ctx.load_test_resource_id}), True),
            (plan("azurerm_resource_group.demo", ["delete"], {"id": "/foreign/group"}), True),
        ):
            with self.subTest(item=item), self.assertRaises(DemoError):
                check_plan(item, ctx, destroy=destroy)

    def test_metrics_are_discovered_and_never_substituted_from_other_tiers(self):
        ctx = context()
        definitions = [
            {"id": f"{ctx.database_id}/providers/Microsoft.Insights/metricDefinitions/{name}",
             "name": {"value": name}, "namespace": NAMESPACE, "supportedAggregationTypes": [aggregation]}
            for name, aggregation in METRICS.items()
        ]
        selected = selected_metrics(definitions, ctx)
        self.assertEqual(len(selected), 8)
        self.assertEqual({value["resourceId"] for value in selected.values()}, {ctx.database_id})
        with self.assertRaises(DemoError):
            selected_metrics(definitions[:-1], ctx)
        definitions[0]["supportedAggregationTypes"] = ["Total"]
        with self.assertRaises(DemoError):
            selected_metrics(definitions, ctx)

    def test_unrequested_azure_defaults_are_removed_without_overwriting_foreign_metrics(self):
        ctx = context()
        selected = {"cpu": {"name": "cpu_percent", "resourceId": ctx.database_id}}
        existing = {"metrics": {
            "cpu": selected["cpu"],
            "automatic-failed-connections": {"name": "connection_failed", "aggregation": "Maximum", "resourceId": ctx.database_id},
        }}
        payload = metric_configuration_patch(existing, selected, ctx)
        self.assertIsNone(payload["metrics"]["automatic-failed-connections"])
        self.assertEqual(payload["metrics"]["cpu"], selected["cpu"])
        existing["metrics"]["automatic-failed-connections"]["resourceId"] = "/foreign/database"
        with self.assertRaises(DemoError):
            metric_configuration_patch(existing, selected, ctx)


class SqlMetricsEvidenceTests(unittest.TestCase):
    def test_engine_evidence_is_bounded_unique_and_never_extracts_files(self):
        _, _, database, _, token = evidence()
        record = {key: database[key] for key in ("run_token", "completed_operations", "account_count", "balance_delta")}
        def archive(records):
            buffer = io.BytesIO()
            text = io.StringIO()
            writer = csv.DictWriter(text, fieldnames=["label", "success", "responseMessage", "timeStamp", "elapsed"])
            writer.writeheader()
            for item in records:
                writer.writerow({"label": "SQLVerify", "success": "true", "responseMessage": "SQL_METRICS_EVIDENCE " + json.dumps(item)})
            for index in range(100):
                writer.writerow({"label": "SQLTransaction", "success": "true", "timeStamp": 1792497615000 + index * 1000, "elapsed": 10})
            with zipfile.ZipFile(buffer, "w") as output:
                output.writestr("../results.csv", text.getvalue())
            return buffer.getvalue()
        result = database_evidence(archive([record]), token)
        self.assertEqual(result["completed_operations"], 100)
        self.assertEqual(result["successful_request_samples"], 100)
        for records in ([], [record, record], [{**record, "credential": "fixture"}]):
            with self.subTest(records=records), self.assertRaises(DemoError):
                database_evidence(archive(records), token)
        with self.assertRaises(DemoError):
            database_evidence(b"not-a-zip", token)
        with self.assertRaises(DemoError):
            database_evidence(b"x" * (10 * 1024 * 1024 + 1), token)

    def test_real_sql_and_nonempty_in_run_metrics_are_both_required(self):
        ctx, run, database, payloads, token = evidence()
        result = verify(run, database, payloads, ctx, token)
        self.assertTrue(result["verified"])
        self.assertEqual(result["committed_sql_operations"], 100)
        self.assertEqual(result["metrics"]["cpu_percent"]["maximum"], 1)
        self.assertEqual(result["metrics"]["connection_successful"]["maximum"], 0)
        self.assertEqual(result["metrics"]["cpu_percent"]["samples"], 1)
        self.assertEqual(len(metric_rows(payloads, run, ctx)), 24)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "metrics.csv"
            write_csv(path, payloads, run, ctx)
            self.assertIn("inside_run", path.read_text())
            self.assertIn(",False", path.read_text())

    def test_idle_database_cannot_be_claimed_as_verified(self):
        ctx, run, database, payloads, token = evidence()
        for metric in payloads[0]["value"]:
            for point in metric["timeseries"][0]["data"]:
                for aggregation in ("average", "total"):
                    if aggregation in point:
                        point[aggregation] = 0
        with self.assertRaisesRegex(TelemetryPending, "idle"):
            verify(run, database, payloads, ctx, token)

    def test_padded_window_does_not_turn_setup_activity_into_run_evidence(self):
        ctx, run, database, payloads, token = evidence()
        for metric in payloads[0]["value"]:
            metric["timeseries"][0]["data"].pop(1)
        with self.assertRaisesRegex(TelemetryPending, "fully run-contained"):
            verify(run, database, payloads, ctx, token)

    def test_server_buckets_need_thirty_seconds_of_actual_workload_overlap(self):
        ctx, run, database, payloads, token = evidence()
        database["workload_start_utc"] = "2026-10-20T12:01:31+00:00"
        with self.assertRaises(TelemetryPending):
            verify(run, database, payloads, ctx, token)
        database["workload_start_utc"] = "2026-10-20T12:01:30+00:00"
        self.assertTrue(verify(run, database, payloads, ctx, token)["verified"])

    def test_missing_nan_and_boolean_samples_are_not_measurements(self):
        for bad in (None, "nan", "Infinity", True):
            ctx, run, database, payloads, token = evidence()
            payloads[0]["value"][0]["timeseries"][0]["data"][1]["average"] = bad
            with self.subTest(bad=bad), self.assertRaises(TelemetryPending):
                verify(run, database, payloads, ctx, token)

    def test_wrong_resource_or_grain_is_rejected(self):
        ctx, run, database, payloads, token = evidence()
        payloads[0]["value"][0]["id"] = payloads[0]["value"][0]["id"].replace("metrics-demo", "other-db")
        with self.assertRaisesRegex(DemoError, "different database"):
            verify(run, database, payloads, ctx, token)
        payloads[0]["interval"] = "PT5M"
        with self.assertRaisesRegex(DemoError, "one-minute"):
            verify(run, database, payloads, ctx, token)

    def test_out_of_range_server_values_are_not_evidence(self):
        for value in (-1, 101):
            ctx, run, database, payloads, token = evidence()
            payloads[0]["value"][0]["timeseries"][0]["data"][1]["average"] = value
            with self.subTest(value=value), self.assertRaisesRegex(DemoError, "invalid SQL metric"):
                verify(run, database, payloads, ctx, token)

    def test_sql_and_client_counts_must_agree(self):
        for key, value in (("run_token", "other-run"), ("completed_operations", 99),
                           ("account_count", 511), ("balance_delta", 101)):
            ctx, run, database, payloads, token = evidence()
            database[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(DemoError, "do not agree"):
                verify(run, database, payloads, ctx, token)

    def test_unsettled_empty_failed_or_weakened_client_results_fail_closed(self):
        changes = [
            {"testResult": "NOT_APPLICABLE"},
            {"passFailCriteria": {}},
            {"testId": "existing-banking-test"},
            {"loadTestConfiguration": {"engineInstances": 2}},
        ]
        for change in changes:
            ctx, run, database, payloads, token = evidence()
            run.update(change)
            with self.subTest(change=change), self.assertRaises(DemoError):
                verify(run, database, payloads, ctx, token)
        ctx, run, database, payloads, token = evidence()
        run["passFailCriteria"]["passFailMetrics"]["no-errors"]["value"] = 100
        with self.assertRaisesRegex(DemoError, "fixed SQL demo contract"):
            verify(run, database, payloads, ctx, token)


class SqlMetricsLifecycleTests(unittest.TestCase):
    @patch.object(demo, "http_json")
    @patch.object(demo, "scoped_token")
    def test_sql_uses_client_id_while_rbac_uses_object_id(self, token, graph):
        ctx = context()
        token.return_value = "fixture-directory-token"
        graph.return_value = {
            "id": ctx.load_test_principal_id, "appId": ctx.load_test_client_id,
            "servicePrincipalType": "ManagedIdentity",
        }
        self.assertEqual(demo.managed_identity_client_id(ctx.subscription_id, ctx.tenant_id, ctx.load_test_principal_id), ctx.load_test_client_id)
        self.assertIn("/servicePrincipals/" + ctx.load_test_principal_id, graph.call_args.args[0])
        self.assertEqual(token.call_args.args[:2], (ctx.subscription_id, ctx.tenant_id))
        self.assertNotEqual(ctx.load_test_client_id, ctx.load_test_principal_id)

    @patch.object(demo, "az_json", return_value={"pricingTier": "Standard"})
    @patch.object(demo, "retail_meter")
    def test_inherited_defender_cost_is_included_before_provisioning(self, meter, azure):
        meter.side_effect = [
            ({"retailPrice": 0.161, "meterId": "basic-fixture"}, "https://prices.azure.com/sql"),
            ({"retailPrice": 15.0, "meterId": "defender-fixture"}, "https://prices.azure.com/defender"),
        ]
        result = demo.retail_cost(context())
        self.assertEqual(result["defender_usd_per_month_estimate"], 15)
        self.assertAlmostEqual(result["ongoing_total_usd_per_730_hours_estimate"], 27.6970833333)
        self.assertGreater(result["ongoing_total_usd_per_day_estimate"], 0.91)
        self.assertEqual(azure.call_args.args[0], context())
        self.assertEqual(azure.call_args.args[1], ["security", "pricing", "show", "--name", "SqlServers"])

    @patch.object(demo, "run")
    def test_tfvars_cannot_change_the_prepared_subscription(self, process):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            variables = context().tfvars()
            variables["subscription_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            write_json(folder / "terraform.tfvars.json", variables)
            with self.assertRaisesRegex(DemoError, "no longer match"):
                demo.terraform(context(), folder, "terraform", ["plan"])
        process.assert_not_called()

    @patch.object(demo, "LoadTest")
    def test_foreign_test_is_never_modified(self, factory):
        client = factory.return_value
        client.optional.return_value = {"description": "existing test owned by someone else"}
        with self.assertRaises(DemoError):
            demo.assert_own_test(client, context(), may_create=True)
        client.request.assert_not_called()

    @patch.object(demo, "stop_owned_run")
    @patch.object(demo, "clear_bootstrap_secret")
    @patch.object(demo, "collect", side_effect=DemoError("fixture interruption"))
    @patch.object(demo, "bootstrap_secret", return_value=("fixture-not-a-real-token", "2030-01-01T00:00:00Z"))
    @patch.object(demo, "configure_test")
    @patch.object(demo, "assert_own_test")
    @patch.object(demo, "LoadTest")
    @patch.object(demo, "current_alt")
    @patch.object(demo, "require_owned_resources")
    def test_ambiguous_start_is_persisted_and_never_retried(
        self, owned, current, factory, own, configure, bootstrap, collect, clear, stop,
    ):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_json(folder / "setup.json", {"initialized": True})
            args = SimpleNamespace(accept_cost=True)
            with self.assertRaisesRegex(DemoError, "fixture interruption"):
                demo.execute_run(context(), folder, args)
            manifest = read_json(folder / "run-manifest.json")
            self.assertTrue(manifest["run_id"].startswith("sql-metrics-"))
            self.assertEqual(factory.return_value.request.call_count, 1)
            request = factory.return_value.request.call_args
            self.assertEqual(request.args[0], "PATCH")
            self.assertFalse(request.args[2]["debugLogsEnabled"])
            self.assertEqual(request.args[2]["requestDataLevel"], "NONE")
            self.assertEqual(request.args[2]["secrets"]["BOOTSTRAP_SQL_TOKEN"]["type"], "SECRET_VALUE")
            from uuid import UUID
            self.assertEqual(request.args[2]["environmentVariables"]["SQL_ENGINE_SID"], UUID(context().load_test_client_id).bytes_le.hex())
            self.assertNotEqual(request.args[2]["environmentVariables"]["SQL_ENGINE_SID"], UUID(context().load_test_principal_id).bytes_le.hex())
            self.assertNotIn("fixture-not-a-real-token", (folder / "run-manifest.json").read_text())
            with self.assertRaisesRegex(DemoError, "already requested"):
                demo.execute_run(context(), folder, args)
            self.assertEqual(factory.return_value.request.call_count, 1)
            stop.assert_called_once()

    def test_only_one_explicit_settled_failure_retry_preserves_prior_evidence(self):
        from unittest.mock import MagicMock
        ctx, failed, _, _, _ = evidence()
        previous = "sql-metrics-" + "a" * 32
        failed.update(testRunId=previous, testResult="FAILED")
        failed["passFailCriteria"]["passFailMetrics"]["no-errors"].update(actualValue=50, result="failed")
        failed["testRunStatistics"]["Total"]["errorPct"] = 50
        client = MagicMock()
        client.get_run.return_value = failed
        args = SimpleNamespace(accept_cost=True, retry_failed_run=previous)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_json(folder / "setup.json", {"configured": True})
            write_json(folder / "run-manifest.json", {"run_id": previous, "run_token": "fixture"})
            write_json(folder / "run.json", failed)
            with (
                patch.object(demo, "current_alt"),
                patch.object(demo, "require_owned_resources"),
                patch.object(demo, "LoadTest", return_value=client),
                patch.object(demo, "assert_own_test"),
                patch.object(demo, "configure_test"),
                patch.object(demo, "bootstrap_secret", return_value=("fixture-secret", "2030-01-01T00:00:00Z")),
                patch.object(demo, "collect", return_value={"verified": True}),
                patch.object(demo, "stop_owned_run"),
                patch.object(demo, "clear_bootstrap_secret", return_value={"removed": False}),
            ):
                demo.execute_run(ctx, folder, args)
                self.assertEqual(read_json(folder / "attempts" / previous / "run.json"), failed)
                latest = read_json(folder / "run-manifest.json")
                self.assertNotEqual(latest["run_id"], previous)
                self.assertEqual(latest["previous_failed_run_id"], previous)
                args.retry_failed_run = latest["run_id"]
                with self.assertRaisesRegex(DemoError, "Only one explicitly authorized retry"):
                    demo.execute_run(ctx, folder, args)
                self.assertEqual(client.request.call_count, 1)

    @patch.object(demo, "LoadTest")
    @patch.object(demo, "current_alt")
    def test_cleanup_requires_exact_confirmation_before_cloud_access(self, current, factory):
        with self.assertRaises(DemoError):
            demo.cleanup(context(), Path("unused"), SimpleNamespace(confirm_name="wrong-demo"))
        current.assert_not_called()
        factory.assert_not_called()

    @patch.object(demo, "scoped_token")
    @patch.object(demo, "token_claims")
    @patch.object(demo, "identity_oid", return_value=context().entra_admin_object_id)
    def test_bootstrap_token_is_short_lived_and_scoped_to_operator(self, oid, claims, token):
        token.return_value = "fixture-not-a-real-token"
        claims.return_value = {"exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())}
        secret, expires = demo.bootstrap_secret(context())
        self.assertEqual(secret, token.return_value)
        self.assertIn("+00:00", expires)
        self.assertEqual(token.call_args.args, (
            context().subscription_id, context().tenant_id, "https://database.windows.net/",
        ))
        claims.return_value = {"exp": int(datetime.now(timezone.utc).timestamp())}
        with self.assertRaisesRegex(DemoError, "expires too soon"):
            demo.bootstrap_secret(context())

    def test_terminal_run_credential_cleanup_cannot_start_another_run(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        snapshot = {"testId": context().test_id, "status": "DONE", "startDateTime": "2026-10-20T12:00:00Z"}
        client.optional.return_value = snapshot
        client.get_run.return_value = snapshot
        with tempfile.TemporaryDirectory() as directory:
            result = demo.clear_bootstrap_secret(client, context(), Path(directory), "sql-metrics-fixture")
        self.assertTrue(result["removed"])
        client.request.assert_called_once_with("PATCH", "test-runs/sql-metrics-fixture", {"secrets": {"BOOTSTRAP_SQL_TOKEN": None}})
        client.request.side_effect = AzureAPIError(404, "Unsupported", "fixture")
        with tempfile.TemporaryDirectory() as directory:
            result = demo.clear_bootstrap_secret(client, context(), Path(directory), "sql-metrics-fixture")
        self.assertFalse(result["removed"])
        self.assertIn("HTTP 404", result["reason"])


if __name__ == "__main__":
    unittest.main()
