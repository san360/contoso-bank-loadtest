import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from tools.gitlab_demo.common import DemoError
from tools.gitlab_demo.vm_exec import execute


class VMAdministrationTests(unittest.TestCase):
    resources = {
        "subscription_id": "target", "resource_group": "demo",
        "gitlab_vm_id": "/subscriptions/target/resourceGroups/demo/providers/Microsoft.Compute/virtualMachines/gitlab",
    }

    def test_scope_and_explicit_success_marker_not_arm_succeeded(self):
        def cli(arguments, subscription, **kwargs):
            self.assertEqual(subscription, "target")
            payload = Path(arguments[arguments.index("--scripts") + 1][1:]).read_text()
            marker = re.search(r"DEMO_VM_[a-f0-9]+", payload)[0]
            return json.dumps({"value": [{"code": "ProvisioningState/succeeded", "message": marker + "_EXIT=0"}]})

        with tempfile.TemporaryDirectory() as name, patch("tools.gitlab_demo.vm_exec.azure", side_effect=cli):
            self.assertIn("_EXIT=0", execute(self.resources, Path(name), "test", "printf completed"))
        with tempfile.TemporaryDirectory() as name, patch(
            "tools.gitlab_demo.vm_exec.azure",
            return_value=json.dumps({"value": [{"code": "ProvisioningState/succeeded", "message": "no actual exit proof"}]}),
        ):
            with self.assertRaises(DemoError):
                execute(self.resources, Path(name), "test", "false")

    def test_other_subscription_or_group_is_refused_before_command(self):
        with tempfile.TemporaryDirectory() as name, patch("tools.gitlab_demo.vm_exec.azure") as cli:
            with self.assertRaises(DemoError):
                execute({**self.resources, "resource_group": "other"}, Path(name), "test", "true")
        cli.assert_not_called()

    def test_private_diagnostic_output_is_not_in_public_error(self):
        with tempfile.TemporaryDirectory() as name, patch(
            "tools.gitlab_demo.vm_exec.azure",
            return_value=json.dumps({"value": [{"message": "fixture-private-diagnostic"}]}),
        ):
            with self.assertRaises(DemoError) as raised:
                execute(self.resources, Path(name), "failure", "false")
        self.assertNotIn("fixture-private-diagnostic", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
