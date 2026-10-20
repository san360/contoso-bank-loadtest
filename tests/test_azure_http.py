import json
import unittest
from unittest.mock import patch

from tools.gitlab_demo.azure_http import ALT_AUDIENCE, ScopedLoadTest
from tools.gitlab_demo.common import DemoError


class ScopedAzureTests(unittest.TestCase):
    subscription = "selected-subscription"
    tenant = "selected-tenant"
    resource_id = "/subscriptions/selected-subscription/resourceGroups/demo/providers/Microsoft.LoadTestService/loadTests/demo"

    @patch("tools.gitlab_demo.azure_http.http_json")
    @patch("tools.gitlab_demo.azure_http.run")
    @patch("tools.gitlab_demo.azure_http.azure")
    def test_shared_default_cannot_override_explicit_subscription(self, arm, cli, http):
        arm.return_value = json.dumps({"properties": {"dataPlaneURI": "test.cnt-prod.loadtesting.azure.com"}})
        cli.return_value = json.dumps({
            "subscription": self.subscription, "tenant": self.tenant,
            "accessToken": "test-fixture-access-token",
        })
        http.return_value = {"status": "DONE"}
        client = ScopedLoadTest(self.subscription, self.tenant, self.resource_id)
        self.assertEqual(client.get_run("run-1"), {"status": "DONE"})
        self.assertEqual(arm.call_args.args[1], self.subscription)
        arguments = cli.call_args.args[0]
        self.assertIn(self.subscription, arguments)
        self.assertIn(ALT_AUDIENCE, arguments)
        self.assertNotIn("test-fixture-access-token", arguments)
        self.assertNotIn("set", arguments)
        self.assertNotIn("login", arguments)
        self.assertEqual(
            http.call_args.kwargs["headers"]["Authorization"], "Bearer test-fixture-access-token"
        )
        self.assertIn("api-version=2025-03-01-preview", http.call_args.args[0])

    @patch("tools.gitlab_demo.azure_http.run")
    @patch("tools.gitlab_demo.azure_http.azure")
    def test_credential_directory_and_subscription_must_match(self, arm, cli):
        arm.return_value = '{"properties":{"dataPlaneURI":"test.cnt-prod.loadtesting.azure.com"}}'
        for wrong in (
            {"subscription": "shared-default", "tenant": self.tenant},
            {"subscription": self.subscription, "tenant": "wrong-directory"},
        ):
            cli.return_value = json.dumps({**wrong, "accessToken": "test-fixture"})
            with self.assertRaises(DemoError):
                ScopedLoadTest(self.subscription, self.tenant, self.resource_id)

    def test_cross_subscription_resource_is_rejected_before_auth(self):
        with self.assertRaises(DemoError):
            ScopedLoadTest("another-subscription", self.tenant, self.resource_id)


if __name__ == "__main__":
    unittest.main()
