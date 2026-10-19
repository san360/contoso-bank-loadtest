import importlib.util
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "runner_config", ROOT / "infra" / "gitlab-demo" / "images" / "runner" / "configure.py"
)
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)


class RunnerConfigTests(unittest.TestCase):
    def test_login_shell_receives_tools_and_aca_identity_not_runner_token(self):
        environment = {
            "GITLAB_URL": "https://gitlab.example",
            "GITLAB_RUNNER_TOKEN": "test-fixture-runner-token",
            "PATH": "/opt/demo-python/bin:/usr/bin",
            "AZURE_EXTENSION_DIR": "/opt/az-extensions",
            "IDENTITY_ENDPOINT": "http://localhost:1234/identity",
            "IDENTITY_HEADER": "test-fixture-local-identity-header",
            "UNRELATED_SECRET": "must-not-be-forwarded",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            CONFIG.configure(path, environment)
            parsed = tomllib.loads(path.read_text())
        self.assertEqual(parsed["concurrent"], 2)
        runner = parsed["runners"][0]
        self.assertEqual(runner["executor"], "shell")
        self.assertEqual(runner["token"], environment["GITLAB_RUNNER_TOKEN"])
        self.assertIn("PATH=" + environment["PATH"], runner["environment"])
        self.assertIn("IDENTITY_HEADER=" + environment["IDENTITY_HEADER"], runner["environment"])
        self.assertIn("AZURE_EXTENSION_DIR=/opt/az-extensions", runner["environment"])
        self.assertFalse(any("GITLAB_RUNNER_TOKEN" in item for item in runner["environment"]))
        self.assertFalse(any("UNRELATED_SECRET" in item for item in runner["environment"]))


if __name__ == "__main__":
    unittest.main()
