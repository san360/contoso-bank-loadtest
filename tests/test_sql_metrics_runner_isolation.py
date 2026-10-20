"""Exercise actual runner functions with CLI stubs; never contact Azure or start load."""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXISTING = ["contoso-bank-pr-gate", "contoso-bank-peak-load", "sql-existing-test"] + [
    f"existing-demo-{index:02}" for index in range(17)
]
OPT_IN = "sql-metrics-isolated-demo"


class SqlMetricsRunnerIsolationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed")
    def test_powershell_all_preserves_old_tests_and_named_execution(self):
        fixtures = [
            {"testId": name, "loadTestConfiguration": {"engineInstances": 1}}
            for name in [*EXISTING, OPT_IN]
        ]
        script = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:RUNNER_PATH, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "Runner syntax errors" }
foreach ($name in @('Get-DeployedTestIds', 'Invoke-TestRun')) {
    $node = $ast.Find({
        param($item)
        $item -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq $name
    }, $true)
    . ([scriptblock]::Create($node.Extent.Text))
}
function Invoke-LoadCli {
    $script:LastCliArgs = @($args[0])
    [pscustomobject]@{ Ok = $true; Output = $env:TEST_FIXTURES }
}
function Get-ObjectPropertyValue($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $Object.PSObject.Properties[$Name].Value
}
function Write-Step {}
function Write-Host {}
$script:MaxRunIdLength = 50
$script:NoWait = $true
$selected = @(Get-DeployedTestIds | ForEach-Object { $_.TestId })
$named = foreach ($id in @('contoso-bank-pr-gate', 'sql-metrics-isolated-demo')) {
    $result = Invoke-TestRun -TestIdToRun $id -ExplicitRunId fixture-run
    [pscustomobject]@{ id = $id; status = $result.Status; cli = $script:LastCliArgs }
}
@{ selected = $selected; named = @($named) } | ConvertTo-Json -Depth 8 -Compress
"""
        result = subprocess.run(
            [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "RUNNER_PATH": str(ROOT / "tools" / "run_loadtest.ps1"),
                 "TEST_FIXTURES": json.dumps(fixtures)},
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
        )
        value = json.loads(result.stdout)
        self.assertEqual(set(value["selected"]), set(EXISTING))
        self.assertNotIn(OPT_IN, value["selected"])
        for named in value["named"]:
            self.assertEqual(named["status"], "STARTED")
            self.assertEqual(named["cli"][:2], ["test-run", "create"])
            self.assertEqual(named["cli"][named["cli"].index("--test-id") + 1], named["id"])

    @unittest.skipUnless(shutil.which("bash"), "Bash is not installed")
    def test_bash_all_preserves_old_tests_and_named_execution(self):
        source = (ROOT / "tools" / "run_loadtest.sh").read_text(encoding="utf-8")
        def function(name):
            match = re.search(r"(?ms)^" + re.escape(name) + r"\(\) \{.*?^\}", source)
            self.assertIsNotNone(match)
            return match.group()
        environment = {
            **os.environ, "TEST_ROWS": "\n".join(f"1\t{name}" for name in [*EXISTING, OPT_IN]),
        }
        stub = """
say_step() { :; }
fail() { printf '%s\\n' "$*" >&2; exit 1; }
load_cli() { printf '%s\\n' "$TEST_ROWS"; }
run_one_test() { printf 'RAN:%s\\n' "$1"; verdict=PASSED; }
LOAD_TEST_NAME=fixture
"""
        result = subprocess.run(
            [shutil.which("bash"), "--noprofile", "--norc", "-s"],
            input=stub + function("run_all_tests") + "\nrun_all_tests\n",
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
        )
        selected = [line.removeprefix("RAN:") for line in result.stdout.splitlines() if line.startswith("RAN:")]
        self.assertEqual(set(selected), set(EXISTING))
        for name in ("contoso-bank-pr-gate", OPT_IN):
            script = """
say_step() { :; }
fail() { printf '%s\\n' "$*" >&2; exit 1; }
load_cli() { printf 'CLI:%s\\n' "$*" >&2; }
NO_WAIT=1
MAX_RUN_ID_LENGTH=50
LOAD_TEST_NAME=fixture
RESOURCE_GROUP=fixture
""" + function("run_one_test") + '\nrun_one_test "$EXPLICIT_ID" fixture-run'
            result = subprocess.run(
                [shutil.which("bash"), "--noprofile", "--norc", "-s"], input=script + "\n",
                env={**environment, "EXPLICIT_ID": name}, capture_output=True, text=True,
                encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CLI:test-run create --test-id " + name, result.stderr)


if __name__ == "__main__":
    unittest.main()
