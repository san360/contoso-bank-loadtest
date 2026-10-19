import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from tools.gitlab_demo.common import DemoError, contract_digest, https_origin, write_json
from tools.gitlab_demo.controller import file_at, gate_evidence, resume_context, wait_merge_status
from tools.gitlab_demo.pipeline import check_mr, feature_name, functional_digest, render_test, sanitize, verify_candidate
from tools.gitlab_demo.verify import LoginForm


class GitLabPipelineTests(unittest.TestCase):
    def test_vm_init_exports_only_public_key_and_no_fabricated_gitlab_pat(self):
        from types import SimpleNamespace
        from tools.gitlab_demo.bootstrap import initialize
        from tools.gitlab_demo.common import read_json

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)

            def keygen(arguments):
                self.assertEqual(arguments[0], "ssh-keygen")
                key = Path(arguments[arguments.index("-f") + 1])
                key.write_text("fixture-private-key")
                key.with_suffix(".pub").write_text("ssh-ed25519 fixture-public-key")
                return ""

            with (
                patch("tools.gitlab_demo.bootstrap.private_directory", return_value=folder),
                patch("tools.gitlab_demo.bootstrap.run", side_effect=keygen),
                patch("builtins.print") as output,
            ):
                initialize(SimpleNamespace(
                    private_dir=folder, subscription="subscription", tenant="tenant", location="swedencentral",
                ))
            variables = read_json(folder / "demo.tfvars.json")
            credentials = read_json(folder / "secrets.json")
            self.assertTrue(variables["deploy_gitlab_vm"])
            self.assertEqual(variables["gitlab_vm_ssh_public_key"], "ssh-ed25519 fixture-public-key")
            self.assertNotIn("gitlab_root_password", variables)
            self.assertNotIn("gitlab_bootstrap_token", variables)
            self.assertEqual(credentials["gitlab_bootstrap_token"], "")
            self.assertNotIn(credentials["gitlab_root_password"], str(output.call_args_list))

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

    def test_login_parser_accepts_the_real_input_or_csrf_meta(self):
        form = LoginForm()
        form.feed('<meta name="csrf-token" content="meta-fixture">')
        self.assertEqual(form.token, "meta-fixture")
        form.feed('<input name="authenticity_token" value="form-fixture">')
        self.assertEqual(form.token, "form-fixture")

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
                    "state": "opened", "source_branch": "demo-merchant-enrichment",
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

    def test_file_reads_keep_slashes_in_queries_not_proxy_path_segments(self):
        class Client:
            project = "projects/1"

            def get(self, suffix):
                if suffix.startswith("/repository/tree?"):
                    return [{"path": "src/transaction_enrichment.py", "type": "blob", "id": "a" * 40}]
                if suffix.startswith("/repository/commits?"):
                    return [{"id": "b" * 40}]
                raise AssertionError(suffix)

            def request(self, method, path, raw=False):
                self_path = "projects/1/repository/blobs/" + "a" * 40 + "/raw"
                if method != "GET" or path != self_path or not raw:
                    raise AssertionError(path)
                return b'FEATURE = "none"\n'

        result = file_at(Client(), "src/transaction_enrichment.py", "main")
        import base64
        self.assertEqual(base64.b64decode(result["content"]), b'FEATURE = "none"\n')
        self.assertEqual(result["last_commit_id"], "b" * 40)

    def test_azure_commands_are_visible_and_the_whole_gate_stays_locked(self):
        import yaml
        from tools.gitlab_demo.common import ROOT
        document = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        gate = "\n".join(document["performance_gate"]["script"])
        for command in (
            "az login --identity", "az account set", "az webapp deploy", "az load test list",
            'az load test "$operation"', "az load test app-component add", "az load test server-metric add",
            "az load test-run create", "az load test-run show", "az load test-run metrics list",
            "az load test-run download-files", "az monitor metrics list", "az monitor log-analytics query",
        ):
            self.assertIn(command, gate)
        self.assertNotIn("pipeline gate", gate)
        self.assertNotIn("set -x", gate)
        self.assertIn('count=$(az load test list', gate)
        self.assertIn("set -euo pipefail", document["default"]["before_script"])
        self.assertEqual(document["performance_gate"]["resource_group"], document["promote_tested_slot"]["resource_group"])
        self.assertIn("az webapp deployment slot swap", "\n".join(document["promote_tested_slot"]["script"]))

    def test_console_sanitizer_removes_url_queries_and_private_headers(self):
        result = sanitize("GET https://example.test/log?sv=fixture&sig=private\nAuthorization: Bearer private\nsafe engine started")
        self.assertNotIn("sig=private", result)
        self.assertNotIn("Bearer private", result)
        self.assertIn("safe engine started", result)

    def test_actual_historical_red_still_fails_the_unchanged_contract(self):
        from tools.gitlab_demo.common import ROOT, read_json
        from tools.loadtest_gate import check_demo_contract, evaluate
        payload = read_json(ROOT / "tests" / "fixtures" / "gitlab-alt-historical-red.json")
        verdict = evaluate(payload)
        check_demo_contract(verdict)
        self.assertEqual(verdict.exit_code, 1)
        self.assertEqual((verdict.samples, verdict.p95_ms, verdict.error_pct), (911, 1250, 0))
        payload["testResult"] = "NOT_APPLICABLE"
        self.assertNotEqual(evaluate(payload).exit_code, 0)

    def test_explicit_resume_rejects_measured_phase_or_a_premature_fix(self):
        import base64
        from tools.gitlab_demo.common import ROOT
        source, base = "b" * 40, "a" * 40
        report = {"status": "stopped", "mr_iid": 1, "base_main_sha": base}

        class Client:
            def get(self, suffix):
                if suffix.startswith("/merge_requests?"):
                    return [{"iid": 1}]
                return {"commit": {"id": base if suffix.endswith("/main") else source}}

        naive = (ROOT / "tools" / "gitlab_demo" / "recipes" / "naive.py").read_text().encode()
        with patch("tools.gitlab_demo.controller.file_at", return_value={"content": base64.b64encode(naive).decode()}):
            self.assertEqual(resume_context(Client(), report, source), (base, 1, source))
            with self.assertRaises(DemoError):
                resume_context(Client(), {**report, "before": {}}, source)
        with patch("tools.gitlab_demo.controller.file_at", return_value={"content": base64.b64encode(b"batched already").decode()}):
            with self.assertRaises(DemoError):
                resume_context(Client(), report, source)


if __name__ == "__main__":
    unittest.main()
