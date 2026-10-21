from __future__ import annotations

from migration_support import MIGRATIONS, ROOT, retail, secrets_plan, source_properties

from configparser import ConfigParser
from math import isclose
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from locust import argument_parser
from locust.dispatch import UsersDispatcher
from locust.runners import WorkerNode
import yaml


def config_values(name: str) -> dict[str, str]:
    config = ConfigParser()
    config.read_string("[locust]\n" + (MIGRATIONS / name).read_text())
    return dict(config["locust"])


class MigrationProfileTests(unittest.TestCase):
    def test_all_locust_configs_parse_with_pinned_runtime_and_only_valid_options(self):
        for name in ("retail.local.conf", "secrets.local.conf", "retail.azure.conf", "secrets.azure.conf"):
            with self.subTest(config=name):
                parser = argument_parser.get_parser(default_config_files=[])
                options = parser.parse_args(["--config", str(MIGRATIONS / name)])
                self.assertEqual(options.run_time, int(config_values(name)["run-time"][:-1]))
                self.assertGreater(options.num_users, 0)
                self.assertGreater(options.spawn_rate, 0)
                self.assertTrue(options.headless)
                self.assertNotIn("percentiles-to-statistics", config_values(name))
                if ".local." in name:
                    self.assertEqual(options.host, "http://127.0.0.1:8000")

    def test_both_local_profiles_preserve_shared_properties_without_azure_environment(self):
        properties = source_properties()
        for name in ("retail.local.conf", "secrets.local.conf"):
            values = config_values(name)
            self.assertEqual(int(values["users"]), int(properties["threads"]))
            self.assertAlmostEqual(
                float(values["spawn-rate"]),
                int(properties["threads"]) / int(properties["rampup"]),
            )
            self.assertEqual(int(values["run-time"][:-1]), int(properties["duration"]))
        source = ET.parse(ROOT / "loadtests" / "jmeter" / "contoso-bank-secrets.jmx")
        self.assertIn("props.getProperty('threads')", source.findtext(
            ".//ThreadGroup/stringProp[@name='ThreadGroup.num_threads']"
        ))

    def test_azure_yaml_paths_types_criteria_autostop_network_and_secret_references(self):
        for name, source_name in (("retail", "peak-load"), ("secrets", "secrets-demo")):
            with self.subTest(migration=name):
                migrated = yaml.safe_load((MIGRATIONS / f"{name}.azure.yaml").read_text())
                source = yaml.safe_load(
                    (ROOT / "loadtests" / "config" / f"{source_name}.yaml").read_text()
                )
                self.assertEqual(migrated["version"], "v0.1")
                self.assertEqual(migrated["testType"], "Locust")
                self.assertNotEqual(migrated["testId"], source["testId"])
                self.assertRegex(migrated["testId"], r"^[a-z0-9_-]{2,50}$")
                self.assertLessEqual(len(migrated["description"]), 100)
                self.assertEqual(migrated["engineInstances"], source["engineInstances"])
                self.assertEqual(migrated["failureCriteria"], source["failureCriteria"])
                self.assertEqual(migrated["autoStop"], source["autoStop"])
                for key in (
                    "subnetId", "publicIPDisabled", "referenceIdentities", "keyVaultReferenceIdentity",
                    "certificates", "appComponents", "secrets",
                ):
                    self.assertEqual(migrated.get(key), source.get(key))
                paths = (
                    [migrated["testPlan"], migrated["properties"]["userPropertyFile"]]
                    + migrated["configurationFiles"]
                )
                for path in paths:
                    self.assertTrue((MIGRATIONS / path).is_file(), path)
                basenames = [Path(path).name for path in paths]
                self.assertEqual(len(basenames), len(set(basenames)))
                env = {item["name"]: item["value"] for item in migrated["env"]}
                self.assertRegex(env["LOCUST_RUN_TIME"], r"^[1-9][0-9]*$")
                conf = config_values(f"{name}.azure.conf")
                self.assertEqual(int(env["LOCUST_USERS"]), int(conf["users"]))
                self.assertEqual(float(env["LOCUST_SPAWN_RATE"]), float(conf["spawn-rate"]))
                self.assertEqual(int(env["LOCUST_RUN_TIME"]), int(conf["run-time"][:-1]))
                self.assertEqual(env["LOCUST_HOST"], conf["host"])
                self.assertNotIn("API_KEY", env)
        self.assertTrue(yaml.safe_load((MIGRATIONS / "retail.azure.yaml").read_text())["splitAllCSVs"])

    def test_retail_aggregate_load_is_literal_jmx_not_the_unread_env_overrides(self):
        source = yaml.safe_load((ROOT / "loadtests" / "config" / "peak-load.yaml").read_text())
        config = yaml.safe_load((MIGRATIONS / "retail.azure.yaml").read_text())
        env = {item["name"]: item["value"] for item in config["env"]}
        properties = source_properties()
        self.assertEqual(int(env["LOCUST_USERS"]), source["engineInstances"] * int(properties["threads"]))
        self.assertEqual(int(env["LOCUST_USERS"]), 200)
        self.assertEqual(int(env["LOCUST_RUN_TIME"]), 300)
        self.assertEqual(int(env["PAGE_SIZE"]), 20)
        self.assertEqual(int(env["THINK_TIME_MS"]), 500)
        self.assertAlmostEqual(float(env["LOCUST_SPAWN_RATE"]), 200 / 60)
        source_env = {item["name"]: item["value"] for item in source["env"]}
        self.assertNotEqual(int(source_env["threads"]) * source["engineInstances"], 200)

    def test_secrets_azure_profile_preserves_effective_groovy_environment_precedence(self):
        source = yaml.safe_load((ROOT / "loadtests" / "config" / "secrets-demo.yaml").read_text())
        config = yaml.safe_load((MIGRATIONS / "secrets.azure.yaml").read_text())
        source_env = {item["name"]: item["value"] for item in source["env"]}
        env = {item["name"]: item["value"] for item in config["env"]}
        self.assertEqual(int(env["LOCUST_USERS"]), int(source_env["threads"]))
        self.assertEqual(int(env["LOCUST_RUN_TIME"]), int(source_env["duration"]))
        self.assertAlmostEqual(
            float(env["LOCUST_SPAWN_RATE"]),
            int(source_env["threads"]) / int(source_env["rampup"]),
        )
        self.assertEqual(env["DEMO_LOG_SECRET"], "false")
        self.assertEqual(env["ACCOUNT_ID"], source_env["accountId"])

    def test_native_dispatcher_matches_per_engine_jmeter_ramp_and_aggregate_stages(self):
        for name, user_class, users, engines, ramp, duration in (
            ("retail", retail.RetailBankingCustomer, 50, 4, 60, 300),
            ("secrets", secrets_plan.SecretsDemoCustomer, 5, 1, 15, 60),
        ):
            with self.subTest(migration=name):
                config = yaml.safe_load((MIGRATIONS / f"{name}.azure.yaml").read_text())
                env = {item["name"]: item["value"] for item in config["env"]}
                per_engine_users = int(env["LOCUST_USERS"]) // engines
                per_engine_rate = float(env["LOCUST_SPAWN_RATE"]) / engines
                self.assertEqual(per_engine_users, users)
                self.assertEqual(int(env["LOCUST_USERS"]) % engines, 0)
                self.assertAlmostEqual(per_engine_rate, users / ramp)
                self.assertEqual(int(env["LOCUST_RUN_TIME"]), duration)
                dispatcher = UsersDispatcher([WorkerNode("local")], [user_class])
                dispatcher.new_dispatch(per_engine_users, per_engine_rate)
                waits = []
                with (
                    patch("locust.dispatch.time.perf_counter", return_value=0),
                    patch("locust.dispatch.gevent.sleep", side_effect=waits.append),
                ):
                    batches = list(dispatcher)
                counts = [batch["local"][user_class.__name__] for batch in batches]
                self.assertEqual(counts, list(range(1, users + 1)))
                self.assertEqual(len(waits), users - 1)
                self.assertTrue(all(isclose(wait, ramp / users) for wait in waits))
                starts = [0.0]
                for wait in waits:
                    starts.append(starts[-1] + wait)
                self.assertAlmostEqual(starts[-1], (users - 1) * ramp / users)
                self.assertEqual(sum(time <= ramp + 1e-9 for time in starts) * engines, users * engines)
                self.assertEqual(sum(time <= 0 for time in starts) * engines, engines)
                if name == "retail":
                    self.assertEqual(sum(time <= 30 + 1e-9 for time in starts) * engines, 104)


if __name__ == "__main__":
    unittest.main()
