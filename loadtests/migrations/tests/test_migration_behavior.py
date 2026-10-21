from __future__ import annotations

from migration_support import (
    BankFixture, MIGRATIONS, ROOT, SYNTHETIC_CSV, controlled_environment,
    environment_for, instantiate, jmeter_runtime, reference_decisions, retail, secrets_plan,
)

import csv
import hashlib
import io
import json
import logging
from pathlib import Path
import tempfile
from time import monotonic
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from gevent import sleep
from locust.exception import StopUser


class MigrationBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = BankFixture()
        self.fixture.start()
        self.addCleanup(self.fixture.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.csv_path = Path(self.temporary.name) / "accounts.csv"
        self.csv_path.write_text(SYNTHETIC_CSV, encoding="utf-8")
        self.events: list[dict] = []

    def create_environment(self, user_class):
        environment = environment_for(user_class, self.fixture.url)
        self.addCleanup(environment.runner.quit)
        environment.events.request.add_listener(lambda **event: self.events.append(event))
        return environment

    def retail_user(self):
        environment = self.create_environment(retail.RetailBankingCustomer)
        with controlled_environment(ACCOUNTS_CSV=str(self.csv_path)):
            retail.initialize(environment)
        self.assertIsNotNone(retail.runtime)
        user = instantiate(retail.RetailBankingCustomer, environment)
        user.on_start()
        self.assertTrue(callable(user.run))
        self.addCleanup(user.on_stop)
        return user, environment

    def secrets_user(self):
        environment = self.create_environment(secrets_plan.SecretsDemoCustomer)
        with controlled_environment(API_KEY=self.fixture.api_key):
            secrets_plan.initialize(environment)
            user = instantiate(secrets_plan.SecretsDemoCustomer, environment)
            with self.assertLogs(level=logging.WARNING):
                user.on_start()
        self.assertTrue(callable(user.run))
        self.addCleanup(user.on_stop)
        return user, environment

    def test_inventory_source_hashes_and_sampler_names(self):
        for folder, source, module in (
            ("retail-banking", "contoso-bank.jmx", retail),
            ("secrets-demo", "contoso-bank-secrets.jmx", secrets_plan),
        ):
            with self.subTest(source=source):
                inventory = json.loads(
                    (MIGRATIONS / folder / "source-inventory.json").read_text()
                )
                plan_path = ROOT / "loadtests" / "jmeter" / source
                self.assertEqual(
                    inventory["sha256"], hashlib.sha256(plan_path.read_bytes()).hexdigest()
                )
                plan = ET.parse(plan_path)
                source_names = [
                    element.attrib["testname"] for element in plan.iter("HTTPSamplerProxy")
                ]
                self.assertEqual(source_names, [item["name"] for item in inventory["samplers"]])
                self.assertTrue(all(name in Path(module.__file__).read_text() for name in source_names))

    def test_shared_percentage_controllers_match_jmeter_for_one_thousand_visits(self):
        controllers = retail.PercentageControllers()
        actual = [controllers.next_iteration() for _ in range(1000)]
        self.assertEqual(actual, reference_decisions(1000))
        self.assertEqual(controllers.executions, [600, 200, 150, 50])
        self.assertTrue(any(sum(choices) > 1 for choices in actual))
        self.assertIn((False, False, False, False), actual)
        self.assertEqual(actual[0], (True, False, False, False))

    def test_csv_uses_actual_file_and_skips_header_recycles_without_random_selection(self):
        path = ROOT / "loadtests" / "data" / "accounts.csv"
        with path.open(encoding="utf-8", newline="") as stream:
            original_rows = list(csv.reader(stream, quoting=csv.QUOTE_NONE))
        accounts = retail.SharedAccounts(path)
        self.assertEqual(accounts.rows, tuple(tuple(row) for row in original_rows[1:]))
        selected = [accounts.next_row() for _ in range(len(accounts.rows) + 1)]
        self.assertEqual(selected[0], selected[-1])
        self.assertEqual(selected[:-1], list(accounts.rows))

    def test_csv_skip_first_is_literal_even_for_headerless_azure_slice(self):
        self.csv_path.write_text(
            "FIRST,Fixture One,00,retail\nSECOND,Fixture Two,01,business\n",
            encoding="utf-8",
        )
        accounts = retail.SharedAccounts(self.csv_path)
        self.assertEqual(accounts.next_row()[0], "SECOND")
        self.assertEqual(accounts.next_row()[0], "SECOND")

    def test_csv_quotes_are_literal_not_csv_escaping(self):
        self.csv_path.write_text(
            'header,header,header,header\n"FIXTURE-A","Fixture Name",00,retail\n',
            encoding="utf-8",
        )
        accounts = retail.SharedAccounts(self.csv_path)
        self.assertEqual(accounts.next_row(), ('"FIXTURE-A"', '"Fixture Name"', "00", "retail"))
        self.csv_path.write_text('h,h,h,h\nA,"Fixture, Name",00,retail\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "four fields"):
            retail.SharedAccounts(self.csv_path)

    def test_csv_empty_header_only_missing_and_bad_width_are_explicit_errors(self):
        for content in ("", "h,h,h,h\n", "h,h,h,h\nA,B,C\n"):
            with self.subTest(content=content):
                self.csv_path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    retail.SharedAccounts(self.csv_path)
        with self.assertRaises(FileNotFoundError):
            retail.SharedAccounts(self.csv_path.with_name("missing.csv"))

    def test_full_order_encoding_correlation_headers_cookies_and_keepalive(self):
        user, _environment = self.retail_user()
        with (
            patch.object(user.state.controllers, "next_iteration", return_value=(True,) * 4),
            patch("jmeter_runtime.sleep") as timer,
            patch("retail_locustfile.random.randrange", return_value=499),
            patch("retail_locustfile.random.randint", return_value=25000),
            patch.object(user.client, "get", wraps=user.client.get) as get,
            patch.object(user.client, "post", wraps=user.client.post) as post,
        ):
            user.banking_iteration()
        records = self.fixture.records
        self.assertEqual(
            [item["operation"] for item in records],
            ["GetBalance", "GetTransactions", "CreateTransfer", "GetTransferStatus", "GenerateStatement"],
        )
        self.assertEqual([item["method"] for item in records], ["GET", "GET", "POST", "GET", "GET"])
        self.assertEqual(records[1]["query"], {"page_size": ["20"]})
        self.assertEqual(
            records[2]["body"],
            '{"from_account":"FIXTURE-A","to_account":"ACC-2002","amount_minor":25000,'
            '"currency":"GBP","reference":"LOADTEST-1-1"}',
        )
        self.assertEqual(records[3]["path"], "/api/transfers/fixture-3")
        self.assertEqual([event["name"] for event in self.events], [item["operation"] for item in records])
        self.assertTrue(all(event["exception"] is None for event in self.events))
        self.assertEqual(timer.call_count, 5)
        self.assertTrue(all(call.args == (0.999,) for call in timer.call_args_list))
        self.assertEqual(records[0]["cookie"], "")
        self.assertTrue(all("fixture_session=local" in record["cookie"] for record in records[1:]))
        self.assertTrue(all(item["accept"] == "application/json" for item in records))
        self.assertTrue(all(item["content_type"] == "application/json" for item in records))
        self.assertEqual(len({item["peer_port"] for item in records}), 1)
        self.assertTrue(all(call.kwargs["timeout"] == (10, 60) for call in get.call_args_list))
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 60))
        self.assertTrue(all(call.kwargs["allow_redirects"] for call in get.call_args_list))
        self.assertTrue(all(adapter.max_retries.total == 0 for adapter in user.client.adapters.values()))
        self.assertFalse(user.client.trust_env)

    def test_one_hundred_iterations_match_reference_trace_and_request_mix(self):
        user, _environment = self.retail_user()
        expected = []
        for index, choices in enumerate(reference_decisions(100)):
            account = ("FIXTURE-A", "FIXTURE-B", "FIXTURE-C")[index % 3]
            if choices[0]:
                expected.append(("GetBalance", f"/api/accounts/{account}/balance"))
            if choices[1]:
                expected.append(("GetTransactions", f"/api/accounts/{account}/transactions"))
            if choices[2]:
                expected.append(("CreateTransfer", "/api/transfers"))
                expected.append(("GetTransferStatus", None))
            if choices[3]:
                expected.append(("GenerateStatement", f"/api/statements/{account}"))
        with patch("jmeter_runtime.sleep") as timer:
            for _ in range(100):
                user.banking_iteration()
        self.assertEqual(len(self.fixture.records), 115)
        self.assertEqual(timer.call_count, 115)
        for record, (operation, path) in zip(self.fixture.records, expected):
            self.assertEqual(record["operation"], operation)
            if path is not None:
                self.assertEqual(record["path"], path)
        for name, count in (
            ("GetBalance", 60), ("GetTransactions", 20), ("CreateTransfer", 15),
            ("GetTransferStatus", 15), ("GenerateStatement", 5),
        ):
            self.assertEqual(sum(event["name"] == name for event in self.events), count)
        bodies = [
            json.loads(record["body"])
            for record in self.fixture.records if record["operation"] == "CreateTransfer"
        ]
        self.assertEqual(
            [body["reference"] for body in bodies],
            [f"LOADTEST-1-{number}" for number in range(1, 16)],
        )
        self.assertTrue(all(500 <= body["amount_minor"] <= 25000 for body in bodies))

    def test_csv_and_controllers_are_shared_but_users_and_iteration_cookies_are_isolated(self):
        first, environment = self.retail_user()
        second = instantiate(retail.RetailBankingCustomer, environment)
        second.on_start()
        self.addCleanup(second.on_stop)
        with (
            patch.object(first.state.controllers, "next_iteration", return_value=(True,) * 4),
            patch("jmeter_runtime.sleep"),
        ):
            first.banking_iteration()
            second.banking_iteration()
            first.banking_iteration()
        first_requests = self.fixture.records[::5]
        self.assertEqual(
            [record["path"] for record in first_requests],
            [
                "/api/accounts/FIXTURE-A/balance",
                "/api/accounts/FIXTURE-B/balance",
                "/api/accounts/FIXTURE-C/balance",
            ],
        )
        self.assertTrue(all(record["cookie"] == "" for record in first_requests))
        self.assertIs(first.state, second.state)
        self.assertIsNot(first.client, second.client)
        references = [
            json.loads(record["body"])["reference"]
            for record in self.fixture.records if record["operation"] == "CreateTransfer"
        ]
        self.assertEqual(references, ["LOADTEST-1-1", "LOADTEST-2-2", "LOADTEST-1-3"])
        self.assertNotEqual(first_requests[0]["peer_port"], first_requests[2]["peer_port"])

    def test_amount_lower_boundary_and_raw_json_integer_type(self):
        user, _environment = self.retail_user()
        with patch("jmeter_runtime.sleep"), patch("retail_locustfile.random.randint", return_value=500):
            user.transfer("FIXTURE-A")
        body = json.loads(self.fixture.records[0]["body"])
        self.assertEqual(body["amount_minor"], 500)
        self.assertIsInstance(body["amount_minor"], int)

    def test_status_assertions_and_continue_on_error(self):
        user, _environment = self.retail_user()
        self.fixture.statuses.update(
            GetBalance=201, GetTransactions=500, CreateTransfer=200, GenerateStatement=204
        )
        with (
            patch.object(user.state.controllers, "next_iteration", return_value=(True,) * 4),
            patch("jmeter_runtime.sleep"),
        ):
            user.banking_iteration()
        self.assertEqual(
            [event["name"] for event in self.events],
            ["GetBalance", "GetTransactions", "CreateTransfer", "GetTransferStatus", "GenerateStatement"],
        )
        self.assertEqual(
            [event["exception"] is not None for event in self.events],
            [True, True, True, False, True],
        )

    def test_failed_create_status_still_follows_real_correlation_under_continue_policy(self):
        user, _environment = self.retail_user()
        self.fixture.statuses["CreateTransfer"] = 400
        with patch("jmeter_runtime.sleep"):
            user.transfer("FIXTURE-A")
        self.assertEqual(
            [event["name"] for event in self.events], ["CreateTransfer", "GetTransferStatus"]
        )
        self.assertIsNotNone(self.events[0]["exception"])
        self.assertIsNone(self.events[1]["exception"])
        self.assertEqual(self.fixture.records[-1]["path"], "/api/transfers/fixture-1")

    def test_bad_missing_or_wrong_type_correlation_fails_creation_and_skips_followup(self):
        user, _environment = self.retail_user()
        for payload in (
            b"not-json", b"{}", b"null", b"[]", b'{"transaction_id":null}',
            b'{"transaction_id":""}', b'{"transaction_id":false}', b'{"transaction_id":{}}',
        ):
            with self.subTest(payload=payload):
                self.fixture.records.clear()
                self.events.clear()
                self.fixture.transfer_response = payload
                with patch("jmeter_runtime.sleep"):
                    user.transfer("FIXTURE-A")
                self.assertEqual([event["name"] for event in self.events], ["CreateTransfer"])
                self.assertIsNotNone(self.events[0]["exception"])
                self.assertEqual(len(self.fixture.records), 1)

    def test_numeric_correlation_uses_actual_response_value(self):
        user, _environment = self.retail_user()
        self.fixture.transfer_response = b'{"transaction_id":12345}'
        with patch("jmeter_runtime.sleep"):
            user.transfer("FIXTURE-A")
        self.assertEqual(self.fixture.records[-1]["path"], "/api/transfers/12345")

    def test_status_followup_has_jmeter_default_http_success_not_an_invented_200_assertion(self):
        user, _environment = self.retail_user()
        for status, fails in ((204, False), (302, False), (404, True)):
            with self.subTest(status=status):
                self.events.clear()
                self.fixture.statuses["GetTransferStatus"] = status
                with patch("jmeter_runtime.sleep"):
                    user.transfer("FIXTURE-A")
                self.assertEqual(self.events[-1]["name"], "GetTransferStatus")
                self.assertEqual(self.events[-1]["exception"] is not None, fails)

    def test_real_timer_precedes_both_requests_in_transfer(self):
        user, _environment = self.retail_user()
        user.state.think_time_ms = 10
        started = monotonic()
        with patch("retail_locustfile.random.randrange", return_value=0):
            user.transfer("FIXTURE-A")
        first, second = self.fixture.records
        self.assertGreaterEqual(first["time"] - started, 0.009)
        self.assertGreaterEqual(second["time"] - first["time"], 0.009)

    def test_timer_bounds_are_500_through_999_ms_and_no_between_task_wait_is_added(self):
        user, _environment = self.retail_user()
        with (
            patch("retail_locustfile.random.randrange", side_effect=[0, 499]) as jitter,
            patch("jmeter_runtime.sleep") as timer,
        ):
            user.before_request()
            user.before_request()
        self.assertEqual([call.args[0] for call in timer.call_args_list], [0.5, 0.999])
        self.assertEqual([call.args for call in jitter.call_args_list], [(500,), (500,)])
        self.assertEqual(user.wait_time(), 0)

    def test_secret_check_once_headers_frozen_per_user_and_no_cookie_manager(self):
        user, _environment = self.secrets_user()
        with controlled_environment(API_KEY=self.fixture.api_key + "-changed"):
            user.banking_iteration()
            user.banking_iteration()
        self.assertEqual(
            [event["name"] for event in self.events],
            [
                "SecretResolutionCheck", "GetBalanceWithSecretHeader",
                "HealthCheckWithSecretHeader", "GetBalanceWithSecretHeader",
                "HealthCheckWithSecretHeader",
            ],
        )
        self.assertEqual(self.events[0]["request_type"], "JSR223")
        self.assertTrue(all(event["exception"] is None for event in self.events))
        self.assertTrue(all(record["authorization_matches"] for record in self.fixture.records))
        self.assertTrue(all(record["cookie"] == "" for record in self.fixture.records))
        self.assertTrue(all(record["accept"] == "application/json" for record in self.fixture.records))
        self.assertTrue(all(record["content_type"] is None for record in self.fixture.records))
        self.assertEqual(user.wait_time(), 0)

    def test_secret_check_is_once_for_each_user_not_once_for_engine(self):
        first, environment = self.secrets_user()
        with controlled_environment(API_KEY=self.fixture.api_key), self.assertLogs(level=logging.WARNING):
            second = instantiate(secrets_plan.SecretsDemoCustomer, environment)
            second.on_start()
        self.addCleanup(second.on_stop)
        self.assertEqual([event["name"] for event in self.events], ["SecretResolutionCheck"] * 2)
        self.assertIsNot(first.client, second.client)

    def test_secret_account_property_has_an_explicit_migration_override(self):
        environment = self.create_environment(secrets_plan.SecretsDemoCustomer)
        with controlled_environment(API_KEY=self.fixture.api_key, ACCOUNT_ID="FIXTURE-OVERRIDE"):
            secrets_plan.initialize(environment)
            user = instantiate(secrets_plan.SecretsDemoCustomer, environment)
            with self.assertLogs(level=logging.WARNING):
                user.on_start()
        self.addCleanup(user.on_stop)
        user.banking_iteration()
        self.assertEqual(self.fixture.records[0]["path"], "/api/accounts/FIXTURE-OVERRIDE/balance")

    def test_no_cookies_on_secrets_redirect_hops_but_retail_redirect_cookies_work(self):
        self.fixture.redirect_balance = True
        secrets_user, _environment = self.secrets_user()
        secrets_user.banking_iteration()
        self.assertEqual(len(self.fixture.records), 3)
        self.assertTrue(all(record["cookie"] == "" for record in self.fixture.records))
        self.assertTrue(all(record["authorization_matches"] for record in self.fixture.records))
        self.fixture.records.clear()
        retail_user, _environment = self.retail_user()
        with patch("jmeter_runtime.sleep"):
            retail_user.banking_iteration()
        self.assertEqual(len(self.fixture.records), 2)
        self.assertIn("fixture_session=local", self.fixture.records[1]["cookie"])
        self.assertEqual(self.events[-1]["name"], "GetBalance")

    def test_secrets_status_failures_are_explicit_and_next_sampler_still_runs(self):
        user, _environment = self.secrets_user()
        self.fixture.statuses.update(GetBalance=201, HealthCheck=503)
        with patch.object(user.client, "get", wraps=user.client.get) as get:
            user.banking_iteration()
        self.assertEqual(len(self.events), 3)
        self.assertTrue(all(event["exception"] is not None for event in self.events[1:]))
        self.assertTrue(all(call.kwargs["timeout"] == (10, 30) for call in get.call_args_list))

    def test_unresolved_secret_records_failure_and_sends_no_http_without_disclosure(self):
        for value in (None, "", " \t\n", "API_KEY", "${__GetSecret(API_KEY)}", "prefix-${missing}"):
            with self.subTest(value=value):
                environment = self.create_environment(secrets_plan.SecretsDemoCustomer)
                overrides = {} if value is None else {"API_KEY": value}
                with controlled_environment(**overrides):
                    secrets_plan.initialize(environment)
                    user = instantiate(secrets_plan.SecretsDemoCustomer, environment)
                    with self.assertLogs(level=logging.ERROR) as logs, self.assertRaises(StopUser):
                        user.on_start()
                self.addCleanup(user.on_stop)
                self.assertEqual(environment.process_exit_code, 1)
                self.assertEqual(self.events[-1]["name"], "SecretResolutionCheck")
                self.assertIsNotNone(self.events[-1]["exception"])
                self.assertEqual(self.fixture.records, [])
                if value and value != "API_KEY":
                    self.assertNotIn(value, "\n".join(logs.output))

    def test_secret_log_has_only_length_and_fingerprint_not_the_value(self):
        environment = self.create_environment(secrets_plan.SecretsDemoCustomer)
        with controlled_environment(API_KEY=self.fixture.api_key):
            secrets_plan.initialize(environment)
            user = instantiate(secrets_plan.SecretsDemoCustomer, environment)
            with self.assertLogs(level=logging.WARNING) as logs:
                user.on_start()
        self.addCleanup(user.on_stop)
        output = "\n".join(logs.output)
        self.assertIn(f"length={len(self.fixture.api_key)}", output)
        self.assertIn(hashlib.sha256(self.fixture.api_key.encode()).hexdigest()[:8], output)
        self.assertNotIn(self.fixture.api_key, output)

    def test_java_trim_and_utf16_secret_length_are_preserved(self):
        self.assertFalse(secrets_plan.secret_is_resolved("\x00\t "))
        self.assertTrue(secrets_plan.secret_is_resolved("\u00a0"))
        environment = self.create_environment(secrets_plan.SecretsDemoCustomer)
        with controlled_environment(API_KEY="\U0001f600"):
            secrets_plan.initialize(environment)
            user = instantiate(secrets_plan.SecretsDemoCustomer, environment)
            with self.assertLogs(level=logging.WARNING) as logs:
                user.on_start()
        self.addCleanup(user.on_stop)
        self.assertIn("length=2", "\n".join(logs.output))

    def test_opt_in_raw_secret_logging_is_rejected_without_disclosure(self):
        environment = self.create_environment(secrets_plan.SecretsDemoCustomer)
        with (
            controlled_environment(DEMO_LOG_SECRET="true", API_KEY=self.fixture.api_key),
            self.assertLogs(level=logging.ERROR) as logs,
        ):
            secrets_plan.initialize(environment)
            sleep(0)
        self.assertIsNone(secrets_plan.runtime)
        self.assertEqual(environment.process_exit_code, 1)
        self.assertNotIn(self.fixture.api_key, "\n".join(logs.output))
        self.assertEqual(self.fixture.records, [])

    def test_invalid_settings_and_missing_csv_fail_startup_without_http(self):
        for override in (
            {"PAGE_SIZE": "bad"}, {"PAGE_SIZE": "0"}, {"THINK_TIME_MS": "-1"},
            {"ACCOUNTS_CSV": str(self.csv_path.with_name("missing.csv"))},
        ):
            with self.subTest(override=override):
                environment = self.create_environment(retail.RetailBankingCustomer)
                with controlled_environment(**override), self.assertLogs(level=logging.ERROR):
                    retail.initialize(environment)
                    sleep(0)
                self.assertIsNone(retail.runtime)
                self.assertEqual(environment.process_exit_code, 1)
        self.assertEqual(self.fixture.records, [])

    def test_deadline_interrupts_timer_and_does_not_start_a_sample(self):
        clock = jmeter_runtime.ScheduledRun(0, 10)
        with (
            patch("jmeter_runtime.monotonic", side_effect=[9.9, 10.0]),
            patch("jmeter_runtime.sleep") as timer,
            self.assertRaises(StopUser),
        ):
            clock.before_sample(500)
        self.assertAlmostEqual(timer.call_args.args[0], 0.1)
        with patch("jmeter_runtime.monotonic", return_value=10), self.assertRaises(StopUser):
            clock.before_sample()

    def test_deadline_after_create_does_not_start_correlated_followup(self):
        user, _environment = self.retail_user()
        user.schedule = jmeter_runtime.ScheduledRun(0, 10)
        with (
            patch("jmeter_runtime.monotonic", side_effect=[0, 0, 10]),
            patch("jmeter_runtime.sleep"),
            self.assertRaises(StopUser),
        ):
            user.transfer("FIXTURE-A")
        self.assertEqual([record["operation"] for record in self.fixture.records], ["CreateTransfer"])

    def test_runtime_rejects_unsupported_distributed_mode_and_invalid_origins(self):
        environment = self.create_environment(retail.RetailBankingCustomer)
        with patch.object(environment, "runner", None):
            with self.assertRaisesRegex(ValueError, "LocalRunner"):
                jmeter_runtime.ScheduledRun.start(environment)
        for host in ("ftp://127.0.0.1", "http://name:password@127.0.0.1", "http://127.0.0.1/path"):
            with self.subTest(host=host), patch.object(environment, "host", host):
                with self.assertRaisesRegex(ValueError, "origin"):
                    jmeter_runtime.ScheduledRun.start(environment)
        with patch.object(environment.parsed_options, "run_time", None):
            with self.assertRaisesRegex(ValueError, "run-time"):
                jmeter_runtime.ScheduledRun.start(environment)


if __name__ == "__main__":
    unittest.main()
