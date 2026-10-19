import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.gitlab_demo.common import DemoError, contract_digest, https_origin, write_json
from tools.gitlab_demo.controller import gate_evidence, wait_merge_status
from tools.gitlab_demo.pipeline import check_mr, feature_name, functional_digest, render_test, verify_candidate


class GitLabPipelineTests(unittest.TestCase):
    def test_https_origins_exclude_credentials_and_paths(self):
        self.assertEqual(https_origin("https://demo.example/"), "https://demo.example")
        for value in ("http://demo.example", "https://u:p@demo.example", "https://demo.example/api"):
            with self.assertRaises(DemoError):
                https_origin(value)

    def test_retargeting_changes_only_endpoint_not_profile_or_criteria(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            before = contract_digest()
            render_test("https://demo.example", target)
            from tools.gitlab_demo.common import ROOT, read_json
            original = read_json(ROOT / "loadtests" / "gitlab-demo" / "transactions.json")
            rendered = read_json(target / "transactions.json")
            rendered["scenarios"]["transactions"]["requests"][0]["endpoint"] = (
                original["scenarios"]["transactions"]["requests"][0]["endpoint"]
            )
            self.assertEqual(original, rendered)
            self.assertEqual(before, contract_digest())

    def test_artifact_source_pipeline_and_content_must_all_match(self):
        source = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            package = folder / "candidate.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("build-info.json", json.dumps({"source_sha": source, "pipeline_id": "42"}))
            manifest = {
                "source_sha": source, "pipeline_id": "42",
                "artifact_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                "contract_sha256": contract_digest(),
            }
            write_json(folder / "candidate.json", manifest)
            self.assertEqual(verify_candidate(source, "42", folder), manifest)
            with self.assertRaises(DemoError):
                verify_candidate("b" * 40, "42", folder)
            with self.assertRaises(DemoError):
                verify_candidate(source, "43", folder)
            package.write_bytes(package.read_bytes() + b"changed")
            with self.assertRaises(DemoError):
                verify_candidate(source, "42", folder)

    def test_feature_output_hash_ignores_only_volatile_booking_time(self):
        row = {"transaction_id": "TX-1", "description": "Shop", "amount_minor": 7,
               "merchant": {"name": "Shop", "category": "Shopping"}, "booked_at": "today"}
        first = {"count": 40, "items": [dict(row) for _ in range(40)]}
        second = {"count": 40, "items": [{**row, "booked_at": "tomorrow"} for _ in range(40)]}
        self.assertEqual(functional_digest(first), functional_digest(second))
        second["items"][0]["amount_minor"] = 8
        self.assertNotEqual(functional_digest(first), functional_digest(second))
        with self.assertRaises(DemoError):
            functional_digest({"count": 0, "items": []})

    def test_controller_rejects_nonperformance_failure(self):
        class Client:
            project = "projects/1"

            def get(self, suffix):
                return [
                    {"name": "functional_tests", "status": "failed"},
                    {"name": "candidate_artifact", "status": "skipped"},
                    {"name": "performance_gate", "status": "skipped"},
                ]

        with self.assertRaises(DemoError):
            gate_evidence(Client(), {"status": "failed", "id": 1}, expect_red=True)
        with self.assertRaises(DemoError):
            gate_evidence(Client(), {"status": "success", "id": 1}, expect_red=True)

    def test_recipe_identification_does_not_execute_module(self):
        self.assertEqual(feature_name('FEATURE = "merchant-category-v1"\nraise Exception()'),
                         "merchant-category-v1")
        with self.assertRaises(DemoError):
            feature_name('FEATURE = str(42)')

    def test_mr_cache_lag_is_bounded_but_a_new_source_fails_closed(self):
        source = "a" * 40

        class Client:
            project_id = "3"
            reads = 0
            branch_sha = source

            def get(self, suffix):
                if suffix.startswith("/repository/branches/"):
                    return {"commit": {"id": self.branch_sha}}
                if suffix.startswith("/merge_requests?"):
                    return [{"iid": 7}]
                self.reads += 1
                return {
                    "state": "opened", "source_branch": "demo/merchant-enrichment",
                    "target_branch": "main", "source_project_id": 3, "target_project_id": 3,
                    "sha": "b" * 40 if self.reads == 1 else source,
                }

        class Context:
            source_sha = source
            client = Client()

        with patch.dict(os.environ, {"CI_MERGE_REQUEST_IID": "7"}), patch(
            "tools.gitlab_demo.pipeline.time.sleep"
        ):
            self.assertEqual(check_mr(Context())["sha"], source)
            Context.client.branch_sha = "c" * 40
            with self.assertRaises(DemoError):
                check_mr(Context())

    def test_merge_cache_can_catch_up_without_accepting_another_pipeline(self):
        source = "a" * 40
        pipeline = {"id": 42, "sha": source, "status": "success"}

        class Client:
            reads = 0
            head_id = 42

            def get(self, suffix):
                if suffix.startswith("/repository/branches/"):
                    return {"commit": {"id": source}}
                statuses = ["ci_must_pass", "checking", "mergeable"]
                status = statuses[min(self.reads, 2)]
                self.reads += 1
                return {
                    "state": "opened", "sha": source, "detailed_merge_status": status,
                    "head_pipeline": {"id": self.head_id, "status": "success"},
                }

        client = Client()
        with patch("tools.gitlab_demo.controller.time.sleep"):
            self.assertEqual(wait_merge_status(client, 7, pipeline, "mergeable")["sha"], source)
            client.head_id = 43
            with self.assertRaises(DemoError):
                wait_merge_status(client, 7, pipeline, "mergeable")


if __name__ == "__main__":
    unittest.main()
