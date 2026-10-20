import ast
from contextlib import redirect_stdout
import io
import logging
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import yaml


ROOT = Path(__file__).resolve().parents[1]


class JMeterSecretDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = ET.parse(ROOT / "loadtests" / "jmeter" / "contoso-bank-secrets.jmx")
        cls.script = cls.plan.findtext(
            ".//JSR223Sampler[@testname='SecretResolutionCheck']/stringProp[@name='script']"
        )

    def test_plaintext_logging_defaults_off_in_the_demo_definition(self):
        config = yaml.safe_load(
            (ROOT / "loadtests" / "config" / "secrets-demo.yaml").read_text()
        )
        env = {item["name"]: item["value"] for item in config["env"]}
        self.assertEqual(env["DEMO_LOG_SECRET"], "false")
        self.assertEqual(config["testType"], "JMX")
        self.assertIn(
            {"SecretResolutionCheck": "percentage(error) > 0"},
            config["failureCriteria"]["clientMetrics"],
        )
        self.assertEqual(config["secrets"][0]["name"], "API_KEY")
        self.assertIn(".vault.azure.net/secrets/", config["secrets"][0]["value"])

    def test_secret_function_remains_outside_the_sampler(self):
        self.assertEqual(
            self.plan.findtext(
                ".//Arguments[@testname='Secret Variables']"
                "/collectionProp/elementProp/stringProp[@name='Argument.value']"
            ),
            "${__GetSecret(API_KEY)}",
        )
        self.assertNotIn("__GetSecret", self.script)

    def test_plaintext_is_guarded_and_not_added_to_sample_results(self):
        self.assertIn("log.warn(msg)", self.script)
        self.assertIn(
            "if ('true'.equalsIgnoreCase(System.getenv('DEMO_LOG_SECRET'))"
            " && ctx.getThreadNum() == 0) {\n"
            "        log.warn('DEMO ONLY: retrieved API_KEY={}', raw)\n"
            "    }",
            self.script,
        )
        self.assertNotIn("SampleResult.setResponseData(raw", self.script)
        self.assertNotIn("SampleResult.setResponseMessage(raw", self.script)
        self.assertNotIn("vars.getThreadNum()", self.script)

    def test_demo_environment_overrides_shared_load_profile_properties(self):
        fields = (
            ("ThreadGroup.num_threads", "threads", "5"),
            ("ThreadGroup.ramp_time", "rampup", "15"),
            ("ThreadGroup.duration", "duration", "60"),
        )
        for field, name, default in fields:
            with self.subTest(field=field):
                self.assertEqual(
                    self.plan.findtext(f".//ThreadGroup/stringProp[@name='{field}']"),
                    "${__groovy(System.getenv('" + name
                    + "') ?: props.getProperty('" + name + "') ?: '" + default + "')}",
                )


class LocustSecretDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse(
            (ROOT / "loadtests" / "locust" / "locustfile.py").read_text(encoding="utf-8")
        )
        hook = next(
            node for node in source.body
            if isinstance(node, ast.FunctionDef) and node.name == "on_test_start"
        )
        hook.decorator_list = []
        module = ast.Module(body=[hook], type_ignores=[])
        namespace = {"logging": logging, "os": os}
        exec(compile(ast.fix_missing_locations(module), "locustfile.py", "exec"), namespace)
        cls.on_test_start = staticmethod(namespace["on_test_start"])

    def setUp(self):
        self.environment = SimpleNamespace(
            host="https://example.invalid", process_exit_code=None, runner=Mock()
        )

    def run_hook(self, env):
        with patch.dict(os.environ, env, clear=True), redirect_stdout(io.StringIO()):
            self.on_test_start(self.environment)

    def test_disabled_logging_does_not_require_or_disclose_a_secret(self):
        for flag in (None, "false", "1", "yes", " true "):
            for secret in (None, "disposable-test-fixture"):
                with self.subTest(flag=flag, secret=secret):
                    env = {}
                    if flag is not None:
                        env["DEMO_LOG_SECRET"] = flag
                    if secret is not None:
                        env["API_KEY"] = secret
                    with self.assertNoLogs(level=logging.WARNING):
                        self.run_hook(env)
                    self.assertIsNone(self.environment.process_exit_code)
                    self.environment.runner.quit.assert_not_called()

    def test_enabled_logging_reports_the_retrieved_value_once(self):
        for flag in ("true", "TRUE", "TrUe"):
            with self.subTest(flag=flag):
                with self.assertLogs(level=logging.WARNING) as captured:
                    self.run_hook({
                        "DEMO_LOG_SECRET": flag,
                        "API_KEY": "disposable-test-fixture",
                    })
                self.assertEqual(
                    [record.getMessage() for record in captured.records],
                    ["DEMO ONLY: retrieved API_KEY=disposable-test-fixture"],
                )
                self.assertEqual(captured.records[0].levelno, logging.WARNING)
                self.assertIsNone(self.environment.process_exit_code)
                self.environment.runner.quit.assert_not_called()

    def test_enabled_logging_fails_closed_without_a_nonempty_secret(self):
        for secret in (None, "", " \t\n"):
            with self.subTest(secret=secret):
                self.environment.runner.reset_mock()
                env = {"DEMO_LOG_SECRET": "true"}
                if secret is not None:
                    env["API_KEY"] = secret
                with self.assertLogs(level=logging.ERROR) as captured:
                    self.run_hook(env)
                self.assertEqual(
                    [record.getMessage() for record in captured.records],
                    ["DEMO_LOG_SECRET=true requires a non-empty API_KEY secret."],
                )
                self.assertEqual(self.environment.process_exit_code, 1)
                self.environment.runner.quit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
