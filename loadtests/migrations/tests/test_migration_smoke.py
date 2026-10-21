from __future__ import annotations

from migration_support import (
    BankFixture, MIGRATIONS, SYNTHETIC_CSV, controlled_environment, stats_from_json,
)

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import monotonic
import unittest


class LocalCliSmokeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = BankFixture()
        self.fixture.start()
        self.addCleanup(self.fixture.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.csv = self.folder / "accounts.csv"
        self.csv.write_text(SYNTHETIC_CSV, encoding="utf-8")

    def run_cli(self, name: str, duration: int, **variables: str):
        prefix = self.folder / name
        for filename in (f"{name}_locustfile.py", f"{name}.local.conf", "jmeter_runtime.py"):
            shutil.copyfile(MIGRATIONS / filename, self.folder / filename)
        command = [
            sys.executable, "-m", "locust",
            "--locustfile", str(self.folder / f"{name}_locustfile.py"),
            "--config", str(self.folder / f"{name}.local.conf"),
            "--host", self.fixture.url,
            "--users", "1", "--spawn-rate", "1",
            "--run-time", f"{duration}s", "--stop-timeout", "2",
            "--only-summary", "--json-file", str(prefix),
        ]
        with controlled_environment(**variables):
            result = subprocess.run(
                command,
                cwd=self.folder,
                env=os.environ.copy(),
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=duration + 20,
                check=False,
            )
        self.assertNotIn(self.fixture.api_key, result.stdout)
        self.assertTrue(prefix.with_suffix(".json").is_file(), result.stdout)
        return result, stats_from_json(prefix.with_suffix(".json"))

    def test_bounded_one_user_retail_cli_covers_every_source_request(self):
        started = monotonic()
        result, stats = self.run_cli("retail", 8, THINK_TIME_MS="0")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertLess(monotonic() - started, 20)
        expected = {
            "GetBalance", "GetTransactions", "CreateTransfer",
            "GetTransferStatus", "GenerateStatement",
        }
        self.assertEqual(set(stats), expected)
        self.assertTrue(all(stats[name]["num_requests"] > 0 for name in expected))
        self.assertEqual(sum(row["num_failures"] for row in stats.values()), 0)
        self.assertIn(
            stats["CreateTransfer"]["num_requests"] - stats["GetTransferStatus"]["num_requests"],
            {0, 1},
        )
        self.assertTrue(all(record["path"].startswith(("/api/", "/health")) for record in self.fixture.records))

    def test_bounded_one_user_secrets_cli(self):
        result, stats = self.run_cli("secrets", 2, API_KEY=self.fixture.api_key)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(stats["SecretResolutionCheck"]["num_requests"], 1)
        self.assertGreater(stats["GetBalanceWithSecretHeader"]["num_requests"], 0)
        self.assertGreater(stats["HealthCheckWithSecretHeader"]["num_requests"], 0)
        self.assertEqual(sum(row["num_failures"] for row in stats.values()), 0)
        self.assertTrue(all(record["authorization_matches"] for record in self.fixture.records))
        self.assertTrue(all(record["cookie"] == "" for record in self.fixture.records))

    def test_missing_secret_cli_exits_nonzero_without_http(self):
        result, stats = self.run_cli("secrets", 1)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.fixture.records, [])
        self.assertEqual(stats["SecretResolutionCheck"]["num_failures"], 1)

    def test_expected_http_assertion_failure_is_not_a_false_green_cli_run(self):
        self.fixture.statuses["GetBalance"] = 201
        result, stats = self.run_cli("secrets", 1, API_KEY=self.fixture.api_key)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertGreater(stats["GetBalanceWithSecretHeader"]["num_failures"], 0)
        self.assertEqual(stats["HealthCheckWithSecretHeader"]["num_failures"], 0)

    def test_invalid_csv_cli_fails_startup_and_sends_no_http(self):
        result, _stats = self.run_cli(
            "retail", 1, ACCOUNTS_CSV=str(self.folder / "does-not-exist.csv")
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.fixture.records, [])
        self.assertIn("Migration startup failed", result.stdout)


if __name__ == "__main__":
    unittest.main()
