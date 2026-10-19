import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import main  # noqa: E402
from merchant_catalog import merchant_details  # noqa: E402


def recipe(name):
    spec = importlib.util.spec_from_file_location(
        f"recipe_{name}", ROOT / "tools" / "gitlab_demo" / "recipes" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TransactionFeatureTests(unittest.TestCase):
    def test_original_transactions_and_query_knob_are_preserved(self):
        async def check():
            async def passthrough(items, query):
                return items

            with patch.object(main.CONFIG, "db_query_ms", 0), patch.object(
                main, "enrich_transactions", passthrough
            ):
                for enabled, count in ((False, 1), (True, 41)):
                    with patch.object(main.CONFIG, "enable_n_plus_one", enabled):
                        start = main.DB.queries_executed
                        result = await main.get_transactions("ACC-1001", 40)
                        self.assertEqual(main.DB.queries_executed - start, count)
                        self.assertEqual(result["count"], 40)
                        self.assertEqual(result["items"][0]["account_id"], "ACC-1001")
        asyncio.run(check())

    def test_both_recipe_commits_have_identical_feature_output(self):
        async def check():
            items = [row.__dict__ for row in main.TRANSACTIONS["ACC-1001"]]
            original = [dict(item) for item in items]
            outputs = []
            for name, expected_queries in (("naive", 40), ("batched", 1)):
                queries = []

                async def query(statement):
                    queries.append(statement)

                result = await recipe(name).enrich_transactions(items, query)
                self.assertEqual(len(queries), expected_queries)
                self.assertEqual(items, original)
                self.assertEqual(result[0]["merchant"], merchant_details(items[0]["description"]))
                self.assertTrue(all("merchant" in row for row in result))
                outputs.append(result)
            self.assertEqual(*outputs)
        asyncio.run(check())

    def test_current_feature_output_remains_functionally_correct(self):
        async def check():
            with patch.object(main.CONFIG, "db_query_ms", 0):
                result = await main.get_transactions("ACC-1001", 40)
            for item in result["items"]:
                if main.FEATURE != "none":
                    self.assertEqual(item["merchant"], merchant_details(item["description"]))
        asyncio.run(check())

    def test_optional_admin_auth_does_not_protect_public_read_endpoints(self):
        with patch.object(main, "DEMO_ADMIN_TOKEN", "test-only-token"):
            with TestClient(main.app) as client:
                self.assertEqual(client.post("/admin/reset").status_code, 401)
                self.assertEqual(
                    client.post("/admin/reset", headers={b"X-Demo-Admin-Token": b"\xff"}).status_code,
                    401,
                )
                self.assertEqual(client.get("/version").status_code, 200)
                self.assertEqual(
                    client.get(
                        "/admin/config", headers={"X-Demo-Admin-Token": "test-only-token"}
                    ).status_code,
                    200,
                )

    def test_local_version_is_explicit_not_a_claimed_commit(self):
        self.assertEqual(main.BUILD_INFO["source_sha"], "local")

    def test_opt_in_request_logs_correlate_source_without_auth_headers(self):
        with patch.object(main, "REQUEST_LOGGING", True), self.assertLogs(
            "contoso.requests", level="INFO"
        ) as logs:
            with TestClient(main.app) as client:
                response = client.get("/health", headers={
                    "X-Correlation-Id": "test-correlation",
                    "X-Demo-Admin-Token": "must-not-be-logged",
                    "Authorization": "must-not-be-logged",
                })
        record = json.loads(logs.records[-1].getMessage())
        self.assertEqual(record["source_sha"], "local")
        self.assertEqual(record["correlation_id"], response.headers["X-Correlation-Id"])
        self.assertEqual(record["status"], 200)
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertNotIn("must-not-be-logged", logs.records[-1].getMessage())


if __name__ == "__main__":
    unittest.main()
