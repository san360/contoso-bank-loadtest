import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

from tools.gitlab_demo.common import ROOT, read_json


class SimplePipelineTests(unittest.TestCase):
    names = ("deploy_load_test", "run_load_test", "view_results_metrics", "view_logs")

    @classmethod
    def setUpClass(cls):
        cls.document = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        cls.jq = os.getenv("JQ") or shutil.which("jq")
        if not cls.jq:
            raise RuntimeError("Pipeline checks require jq, already present in the demo runner")

    def test_presenter_has_four_named_stages_and_visible_native_commands(self):
        jobs = {key for key, value in self.document.items() if not key.startswith(".") and isinstance(value, dict) and "script" in value}
        self.assertEqual(jobs, set(self.names))
        self.assertLessEqual(len((ROOT / ".gitlab-ci.yml").read_text().splitlines()), 150)
        for name in self.names:
            self.assertEqual(self.document[name]["stage"], name)
            self.assertEqual(self.document[name]["extends"], ".cli_stage")
            self.assertNotIn("python", "\n".join(self.document[name]["script"]).lower())
        self.assertIn("az load test update", "\n".join(self.document["deploy_load_test"]["script"]))
        self.assertIn("az load test-run create", "\n".join(self.document["run_load_test"]["script"]))
        self.assertIn("az load test-run metrics list", "\n".join(self.document["view_results_metrics"]["script"]))
        self.assertIn("az load test-run download-files", "\n".join(self.document["view_logs"]["script"]))

    def test_evidence_viewers_survive_failed_gate_and_read_one_artifact_run_id(self):
        self.assertEqual(self.document[".cli_stage"]["artifacts"]["when"], "always")
        for name in self.names[1:]:
            script = "\n".join(self.document[name]["script"])
            self.assertIn("RUN_ID=$(cat demo-evidence/run-id.txt)", script)
            self.assertIn('"cli-$CI_PIPELINE_ID"', script)
            self.assertNotIn("--latest", script)
            self.assertEqual(self.document[name]["dependencies"], ["run_load_test"] if name.startswith("view_") else ["deploy_load_test"])
        for name in self.names[2:]:
            self.assertEqual(self.document[name]["when"], "always")
            self.assertNotIn("allow_failure", self.document[name])
        gate = "\n".join(self.document["run_load_test"]["script"])
        self.assertIn("performance_failed) exit 1", gate)
        self.assertIn("*) exit 2", gate)
        self.assertNotIn("allow_failure", self.document["run_load_test"])

    def test_run_rechecks_actual_definition_profile_and_source_under_the_shared_lock(self):
        script = "\n".join(self.document["run_load_test"]["script"])
        create = script.index("az load test-run create")
        for check in ("definition-at-run.json", "az load test file download", "verified-profile/transactions.json", "deployed-version.json"):
            self.assertLess(script.index(check), create)
        self.assertGreater(script.index("deployed-version-after.json"), create)
        advanced = yaml.safe_load((ROOT / ".gitlab" / "advanced.yml").read_text())
        self.assertEqual(self.document["run_load_test"]["resource_group"], advanced["performance_gate"]["resource_group"])
        self.assertNotIn("az webapp", "\n".join(line for name in self.names for line in self.document[name]["script"]))

    def test_same_strict_filter_keeps_real_red_and_invalid_states_nonzero(self):
        red = read_json(ROOT / "tests" / "fixtures" / "gitlab-alt-historical-red.json")
        for status, result, expected in (
            ("DONE", "FAILED", "performance_failed"),
            ("DONE", "NOT_APPLICABLE", "pending"),
            ("CANCELLED", "FAILED", "unjudgeable"),
            ("VALIDATION_FAILURE", "FAILED", "unjudgeable"),
        ):
            payload = {**red, "status": status, "testResult": result}
            response = subprocess.run(
                [self.jq, "-f", str(ROOT / "loadtests" / "gitlab-demo" / "verdict.jq")],
                input=json.dumps(payload), text=True, capture_output=True, check=True,
            )
            self.assertEqual(json.loads(response.stdout)["outcome"], expected)

    def test_actual_shell_gate_exit_does_not_hide_a_measured_failure(self):
        bash = shutil.which("bash")
        if os.name == "nt":
            bash = str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
        if not bash or not Path(bash).is_file():
            raise RuntimeError("Pipeline shell validation requires Bash, already present in the demo runner")
        statement = self.document["run_load_test"]["script"][-1]
        for outcome, expected in (("passed", 0), ("performance_failed", 1), ("pending", 2), ("unjudgeable", 2)):
            with tempfile.TemporaryDirectory() as name:
                folder = Path(name)
                (folder / "demo-evidence").mkdir()
                (folder / "demo-evidence" / "verdict.json").write_text(json.dumps({"outcome": outcome}))
                # A shell fixture isolates the production exit statement, not Azure execution.
                script = "jq() { printf '%s\\n' " + outcome + "; }\n" + statement
                result = subprocess.run([bash, "-c", script], cwd=folder, capture_output=True)
                self.assertEqual(result.returncode, expected)


if __name__ == "__main__":
    unittest.main()
