from __future__ import annotations

import configparser
import csv
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

try:
    import locust
except ModuleNotFoundError as exc:
    if exc.name != "locust":
        raise
    LOCUST_AVAILABLE = False
else:
    LOCUST_AVAILABLE = True
    from gevent.pywsgi import WSGIServer
    from locust.dispatch import UsersDispatcher
    from locust.env import Environment
    from locust.event import Events
    from locust.exception import StopUser
    from locust.runners import WorkerNode

from unittest.mock import patch

import yaml

from tools import set_target_host


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "loadtests" / "locust" / "secrets_locustfile.py"
CONFIG = ROOT / "loadtests" / "locust" / "secrets.conf"
FIXTURE_KEY = "local-fixture-not-a-real-credential"
BALANCE = "/api/accounts/ACC-1001/balance"
REQUIRES_LOCUST = "Install loadtests/locust/requirements.txt to run Locust checks."


def load_script():
    spec = importlib.util.spec_from_file_location("secrets_locustfile", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_yaml(name):
    return yaml.safe_load(
        (ROOT / "loadtests" / "config" / name).read_text(encoding="utf-8")
    )


class KeyVaultLocustConfigTests(unittest.TestCase):
    def test_distinct_test_preserves_secret_references_and_failure_criteria(self):
        source = read_yaml("secrets-demo.yaml")
        target = read_yaml("secrets-demo-locust.yaml")
        self.assertEqual(target["testId"], "contoso-bank-secrets-kv-locust")
        self.assertNotEqual(target["testId"], source["testId"])
        self.assertEqual(target["testType"], "Locust")
        self.assertEqual(target["secrets"], source["secrets"])
        self.assertEqual(target["failureCriteria"], source["failureCriteria"])
        self.assertEqual(target["autoStop"], source["autoStop"])
        self.assertLessEqual(len(target["description"]), 100)
        base = ROOT / "loadtests" / "config"
        self.assertEqual((base / target["testPlan"]).resolve(), SCRIPT)
        self.assertEqual(
            (base / target["properties"]["userPropertyFile"]).resolve(), CONFIG
        )

    def test_native_profile_matches_jmx_demo_and_single_engine_total(self):
        source = read_yaml("secrets-demo.yaml")
        target = read_yaml("secrets-demo-locust.yaml")
        source_env = {item["name"]: item["value"] for item in source["env"]}
        target_env = {item["name"]: item["value"] for item in target["env"]}
        profile = configparser.ConfigParser()
        profile.read_string("[locust]\n" + CONFIG.read_text(encoding="utf-8"))
        users = int(target_env["LOCUST_USERS"])
        spawn_rate = float(target_env["LOCUST_SPAWN_RATE"])
        self.assertEqual(target["engineInstances"], 1)
        self.assertEqual(users, int(source_env["threads"]))
        self.assertAlmostEqual(users / spawn_rate, int(source_env["rampup"]))
        self.assertEqual(int(target_env["LOCUST_RUN_TIME"]), int(source_env["duration"]))
        self.assertEqual(profile.getint("locust", "users"), users)
        self.assertEqual(profile.getfloat("locust", "spawn-rate"), spawn_rate)
        self.assertEqual(profile["locust"]["run-time"], target_env["LOCUST_RUN_TIME"] + "s")
        self.assertEqual(profile["locust"]["host"], target_env["LOCUST_HOST"])
        self.assertTrue(profile.getboolean("locust", "headless"))
        self.assertEqual(target_env["accountId"], source_env["accountId"])
        self.assertEqual(target_env["DEMO_LOG_SECRET"], "false")
        self.assertNotIn("API_KEY", target_env)
        self.assertNotIn("splitAllCSVs", target)

    def test_retargeting_and_workflow_include_new_test_without_secret_override(self):
        self.assertIn(
            "loadtests/config/secrets-demo-locust.yaml", set_target_host.TARGET_FILES
        )
        self.assertIn("loadtests/locust/secrets.conf", set_target_host.TARGET_FILES)
        workflow = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "loadtest.yml").read_text(encoding="utf-8")
        )
        steps = workflow["jobs"]["loadtest"]["steps"]
        select = next(step for step in steps if step.get("id") == "selected")
        self.assertIn(
            "contoso-bank-secrets-kv-locust)\n"
            "    config='loadtests/config/secrets-demo-locust.yaml'",
            select["run"],
        )
        run = next(step for step in steps if step.get("id") == "run-load-test")
        self.assertIn(
            "steps.selected.outputs.test_id == 'contoso-bank-secrets-kv-locust'",
            run["with"]["secrets"],
        )
        self.assertIn("&& '[]'", run["with"]["secrets"])
        self.assertIn("toJSON(secrets.DEMO_API_KEY)", run["with"]["secrets"])


class LocustTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = load_script()

    def setUp(self):
        self.samples = []
        self.environment = Environment(
            host="http://127.0.0.1:1",
            events=Events(),
            user_classes=[self.script.KeyVaultSecretsCustomer],
        )
        self.environment.events.request.add_listener(
            lambda **sample: self.samples.append(sample)
        )
        self.script.reset_secret_demo_logging(self.environment)
        host = patch.object(
            self.script.KeyVaultSecretsCustomer, "host", self.environment.host
        )
        host.start()
        self.addCleanup(host.stop)
        self.user = self.script.KeyVaultSecretsCustomer(self.environment)
        self.addCleanup(self.user.client.close)

    def start_user(self, **extra):
        with patch.dict(os.environ, {"API_KEY": FIXTURE_KEY, **extra}, clear=True):
            with self.assertLogs(level="WARNING") as captured:
                self.user.on_start()
        return [record.getMessage() for record in captured.records]


@unittest.skipUnless(LOCUST_AVAILABLE, REQUIRES_LOCUST)
class KeyVaultLocustUserTests(LocustTestCase):
    def test_secret_check_is_measured_without_disclosing_value(self):
        messages = self.start_user()
        fingerprint = hashlib.sha256(FIXTURE_KEY.encode()).hexdigest()[:8]
        self.assertEqual(
            messages,
            [f"Key Vault secret API_KEY resolved. length={len(FIXTURE_KEY)} "
             f"sha256_prefix={fingerprint}"],
        )
        self.assertNotIn(FIXTURE_KEY, repr(self.samples))
        self.assertEqual(len(self.samples), 1)
        self.assertEqual(self.samples[0]["name"], "SecretResolutionCheck")
        self.assertEqual(self.samples[0]["request_type"], "SECRET")
        self.assertIsNone(self.samples[0]["exception"])
        self.assertGreaterEqual(self.samples[0]["response_time"], 0)
        self.assertGreater(self.samples[0]["response_length"], 0)

    def test_missing_blank_unresolved_or_invalid_header_secret_fails_closed(self):
        for value in (None, "", " \t\n", "API_KEY", "${__GetSecret(API_KEY)}", "bad\r\nkey"):
            with self.subTest(value=value):
                self.samples.clear()
                env = {} if value is None else {"API_KEY": value}
                with patch.dict(os.environ, env, clear=True):
                    with self.assertLogs(level="ERROR") as captured:
                        with self.assertRaises(StopUser):
                            self.user.on_start()
                self.assertEqual(len(self.samples), 1)
                self.assertIsInstance(self.samples[0]["exception"], RuntimeError)
                self.assertEqual(self.environment.process_exit_code, 1)
                self.assertNotIn("Authorization", self.user.client.headers)
                self.assertIn("Key Vault Secrets User", captured.output[0])
                if value and value.strip() and value != "API_KEY":
                    self.assertNotIn(value, repr(self.samples))

    def test_plaintext_requires_exact_opt_in_and_logs_once_per_process_per_run(self):
        for flag in ("true", "TRUE", "TrUe"):
            with self.subTest(flag=flag):
                self.script.reset_secret_demo_logging(self.environment)
                messages = self.start_user(DEMO_LOG_SECRET=flag)
                another = self.script.KeyVaultSecretsCustomer(self.environment)
                self.addCleanup(another.client.close)
                with patch.dict(os.environ, {"API_KEY": FIXTURE_KEY, "DEMO_LOG_SECRET": flag}):
                    with self.assertLogs(level="WARNING") as captured:
                        another.on_start()
                messages += [record.getMessage() for record in captured.records]
                self.assertEqual(
                    messages.count(f"DEMO ONLY: retrieved API_KEY={FIXTURE_KEY}"), 1
                )
                self.assertNotIn(FIXTURE_KEY, repr(self.samples))

    def test_other_demo_flags_do_not_disclose_secret(self):
        for flag in ("false", "1", "yes", " true ", ""):
            with self.subTest(flag=flag):
                self.script.reset_secret_demo_logging(self.environment)
                self.assertNotIn(
                    FIXTURE_KEY, "\n".join(self.start_user(DEMO_LOG_SECRET=flag))
                )

    def test_order_headers_timeouts_and_lifetimes_match_jmx(self):
        self.start_user(accountId="ACC-2007")
        plan = ET.parse(ROOT / "loadtests" / "jmeter" / "contoso-bank-secrets.jmx")
        samplers = plan.findall(".//HTTPSamplerProxy")
        headers = {
            item.findtext("stringProp[@name='Header.name']"):
                item.findtext("stringProp[@name='Header.value']").replace(
                    "${udv_apiKey}", FIXTURE_KEY
                )
            for item in plan.findall(".//HeaderManager/collectionProp/elementProp")
        }
        for key, value in headers.items():
            self.assertEqual(self.user.client.headers[key], value)
        with patch.dict(os.environ, {"API_KEY": "changed", "accountId": "ACC-2999"}):
            with patch.object(self.user.client, "get") as get:
                get.return_value.__enter__.return_value.status_code = 200
                self.user.fetch_balance_and_health()
                self.user.fetch_balance_and_health()
        self.assertEqual(len(get.call_args_list), 4)
        for call, sampler in zip(get.call_args_list, samplers * 2):
            self.assertEqual(sampler.findtext("stringProp[@name='HTTPSampler.method']"), "GET")
            self.assertEqual(
                call.args[0],
                sampler.findtext("stringProp[@name='HTTPSampler.path']").replace(
                    "${accountId}", "ACC-2007"
                ),
            )
            self.assertEqual(call.kwargs["name"], sampler.get("testname"))
            self.assertEqual(call.kwargs["timeout"], (10, 30))
            self.assertTrue(call.kwargs["allow_redirects"])
            self.assertTrue(call.kwargs["catch_response"])
        self.assertEqual(self.user.client.headers["Authorization"], f"Bearer {FIXTURE_KEY}")
        self.assertEqual(len(self.samples), 1)
        self.assertEqual(self.user.wait_time(), 0)

    def test_native_dispatch_matches_ramp_and_can_stop_without_http(self):
        profile = read_yaml("secrets-demo-locust.yaml")
        env = {item["name"]: item["value"] for item in profile["env"]}
        dispatcher = UsersDispatcher(
            [WorkerNode("engine")], [self.script.KeyVaultSecretsCustomer]
        )
        clock = [0.0]
        points = []
        with patch("locust.dispatch.time.perf_counter", side_effect=lambda: clock[0]):
            with patch(
                "locust.dispatch.gevent.sleep",
                side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            ):
                dispatcher.new_dispatch(
                    int(env["LOCUST_USERS"]), float(env["LOCUST_SPAWN_RATE"])
                )
                for batch in dispatcher:
                    points.append((clock[0], sum(batch["engine"].values())))
                self.assertEqual(points, [(0, 1), (3, 2), (6, 3), (9, 4), (12, 5)])
                clock[0] = int(env["LOCUST_RUN_TIME"])
                dispatcher.new_dispatch(0, 5)
                self.assertEqual(
                    [sum(batch["engine"].values()) for batch in dispatcher], [0]
                )
        self.assertEqual(self.samples, [])


class HttpFixture:
    def __init__(self):
        self.requests = []
        self.statuses = {}
        self.redirect = False
        self.server = WSGIServer(("127.0.0.1", 0), self.respond, log=None)
        self.server.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def respond(self, environ, start_response):
        path = environ["PATH_INFO"]
        self.requests.append({
            "method": environ["REQUEST_METHOD"],
            "path": path,
            "authorization": environ.get("HTTP_AUTHORIZATION"),
            "accept": environ.get("HTTP_ACCEPT"),
            "cookie": environ.get("HTTP_COOKIE"),
            "query": environ["QUERY_STRING"],
            "body": environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0)),
        })
        headers = [("Content-Type", "application/json"), ("Set-Cookie", "fixture=value")]
        status = self.statuses.get(path, "200 OK")
        if self.redirect and path == BALANCE:
            status = "302 Found"
            headers.append(("Location", "/redirected-balance"))
        start_response(status, headers)
        return [b"" if status.startswith("204 ") else b'{"ok":true}']


@unittest.skipUnless(LOCUST_AVAILABLE, REQUIRES_LOCUST)
class KeyVaultLocustHttpTests(LocustTestCase):
    def setUp(self):
        super().setUp()
        self.fixture = HttpFixture()
        self.addCleanup(self.fixture.server.stop)
        self.user.client.base_url = self.fixture.url
        self.user.client.trust_env = False

    def test_recorded_trace_and_cookies_match_two_ordered_iterations(self):
        self.start_user()
        self.user.fetch_balance_and_health()
        self.user.fetch_balance_and_health()
        self.assertEqual(
            [request["path"] for request in self.fixture.requests],
            [BALANCE, "/health", BALANCE, "/health"],
        )
        for request in self.fixture.requests:
            self.assertEqual(request["method"], "GET")
            self.assertEqual(request["authorization"], f"Bearer {FIXTURE_KEY}")
            self.assertEqual(request["accept"], "application/json")
            self.assertIsNone(request["cookie"])
            self.assertEqual(request["query"], "")
            self.assertEqual(request["body"], b"")
        self.assertEqual(
            [sample["name"] for sample in self.samples],
            ["SecretResolutionCheck", "GetBalanceWithSecretHeader", "HealthCheckWithSecretHeader",
             "GetBalanceWithSecretHeader", "HealthCheckWithSecretHeader"],
        )
        self.assertTrue(all(sample["exception"] is None for sample in self.samples))

    def test_non_200_responses_fail_and_balance_failure_does_not_skip_health(self):
        self.start_user()
        self.fixture.statuses = {BALANCE: "401 Unauthorized", "/health": "204 No Content"}
        self.user.fetch_balance_and_health()
        self.assertEqual(
            [request["path"] for request in self.fixture.requests], [BALANCE, "/health"]
        )
        self.assertEqual(
            [str(sample["exception"]) for sample in self.samples[1:]],
            ["Expected 200, got 401", "Expected 200, got 204"],
        )

    def test_redirects_are_followed_under_original_sampler_name(self):
        self.start_user()
        self.fixture.redirect = True
        self.user.fetch_balance_and_health()
        self.assertEqual(
            [request["path"] for request in self.fixture.requests],
            [BALANCE, "/redirected-balance", "/health"],
        )
        self.assertEqual(self.samples[1]["name"], "GetBalanceWithSecretHeader")
        self.assertIsNone(self.samples[1]["exception"])
        self.assertIsNone(self.fixture.requests[-1]["cookie"])

    def run_headless(self, api_key):
        env = {key: value for key, value in os.environ.items() if not key.startswith("LOCUST_")}
        env.update(DEMO_LOG_SECRET="false", accountId="ACC-1001", NO_PROXY="127.0.0.1,localhost")
        env.pop("API_KEY", None)
        if api_key is not None:
            env["API_KEY"] = api_key
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "smoke"
            result = subprocess.run(
                [
                    sys.executable, "-m", "locust", "-f", str(SCRIPT),
                    "--config", str(CONFIG), "--host", self.fixture.url,
                    "--users", "1", "--spawn-rate", "1", "--run-time", "2s",
                    "--csv", str(prefix), "--only-summary",
                ],
                cwd=directory, env=env, capture_output=True, text=True, timeout=30,
            )
            with prefix.with_name("smoke_stats.csv").open(newline="", encoding="utf-8") as handle:
                stats = {row["Name"]: row for row in csv.DictReader(handle)}
        return result, stats

    def test_one_user_headless_run_records_all_three_sampler_names(self):
        result, stats = self.run_headless(FIXTURE_KEY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(int(stats["SecretResolutionCheck"]["Request Count"]), 1)
        for name in ("SecretResolutionCheck", "GetBalanceWithSecretHeader", "HealthCheckWithSecretHeader"):
            self.assertGreater(int(stats[name]["Request Count"]), 0)
            self.assertEqual(int(stats[name]["Failure Count"]), 0)
        self.assertEqual(stats["SecretResolutionCheck"]["Type"], "SECRET")
        self.assertNotIn(FIXTURE_KEY, result.stdout + result.stderr)
        self.assertGreaterEqual(len(self.fixture.requests), 2)
        for index, request in enumerate(self.fixture.requests):
            self.assertEqual(request["path"], (BALANCE, "/health")[index % 2])
            self.assertEqual(request["authorization"], f"Bearer {FIXTURE_KEY}")
            self.assertIsNone(request["cookie"])

    def test_missing_secret_headless_run_is_nonzero_and_sends_no_http(self):
        result, stats = self.run_headless(None)
        self.assertNotEqual(result.returncode, 0)
        self.assertGreater(int(stats["SecretResolutionCheck"]["Failure Count"]), 0)
        self.assertEqual(self.fixture.requests, [])


if __name__ == "__main__":
    unittest.main()
