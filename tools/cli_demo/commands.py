"""Allowlisted CLI operations, shell-safe previews, and portable metric exports."""

from __future__ import annotations

import csv
import io
import json
import re
import shlex
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[2]
GUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,49}\Z")
RESOURCE_NAME = re.compile(r"[A-Za-z0-9_().][A-Za-z0-9_().-]{0,89}\Z")
SECRET_KEY = re.compile(
    r"(?i)(password|passwd|secret|token|authorization|api[-_]?key|"
    r"connection[-_]?string|^sig$|^sas$)"
)
TEST_LIST_COLUMNS = ("Test ID", "Display name", "Type", "Engines")
INSIGHTS_API_VERSION = "2026-04-01"
LOAD_TEST_AUDIENCE = "https://cnt-prod.loadtesting.azure.com"


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    group: str
    description: str
    confirmation: str = ""


ACTIONS = (
    Action("account", "Check signed-in account", "setup",
           "Shows the active CLI subscription and tenant, not just a cached account matching the form."),
    Action("select-subscription", "Select CLI subscription", "setup",
           "Explicitly selects the configured subscription in this CLI profile. Use a separate AZURE_CONFIG_DIR for isolation.",
           "This changes the active Azure CLI subscription for other terminals using this profile. I authorize this change."),
    Action("subscriptions", "Find subscriptions", "setup",
           "Lists enabled subscriptions available to the current CLI identity."),
    Action("resources", "Find Load Testing resources", "setup",
           "Discovers existing resources in the selected subscription; creates nothing."),
    Action("list-tests", "List test definitions", "lifecycle",
           "Choose JSON or readable TSV columns. A definition is a reusable script and configuration, not an execution."),
    Action("create-test", "Create the small demo test", "lifecycle",
           "Uses YAML generated with your Test ID and authorized target: one engine, five users, 60 seconds, GET only.",
           "This creates the selected test ID. I have permission to test the target."),
    Action("show-test", "Inspect test definition", "lifecycle",
           "Checks the uploaded configuration before spending virtual user hours."),
    Action("run-test", "Start a load test run", "lifecycle",
           "Starts the selected definition with --no-wait. Traffic and size come from that definition, not the target URL field.",
           "This starts real traffic and incurs Azure charges. I am authorized to test this target."),
    Action("list-runs", "List runs for this test", "lifecycle",
           "Select an existing run to inspect or generate a fresh ID before starting another."),
    Action("show-run", "Inspect run and verdict", "lifecycle",
           "Shows execution status, fail criteria, and request statistics. DONE alone is not a pass."),
    Action("stop-run", "Stop selected run", "lifecycle",
           "Stops the Azure run, not just a browser polling loop.",
           "I want to stop the selected Azure load test run."),
    Action("namespaces", "Discover metric namespaces", "metrics",
           "LoadTestRunMetrics is client traffic; EngineHealthMetrics is generator health."),
    Action("client-definitions", "Discover client metrics", "metrics",
           "Lists supported metric names, units, dimensions and aggregations."),
    Action("all-client-metrics", "Get all client metrics", "metrics",
           "Fetches definitions and every client metric for the selected run, with named charts and JSON/CSV downloads."),
    Action("client-metrics", "Get one client metric", "metrics",
           "Fetches one named client metric through az load and exports JSON plus CSV."),
    Action("engine-metrics", "Get engine health", "metrics",
           "Fetches generator CPU, not application CPU. Exports JSON plus CSV."),
    Action("run-components", "List run app components", "metrics",
           "Lists monitored Azure resources associated with this run, NOT measured values."),
    Action("run-server-config", "List run server metric configuration", "metrics",
           "Lists which server metrics are configured, NOT their time-series values."),
    Action("component-details", "Inspect application resource", "server",
           "Resolves an existing ARM ID to its name, type and kind; does not modify it."),
    Action("attach-component", "Attach component to test", "server",
           "Registers the inspected Azure resource for monitoring on future runs.",
           "This updates app-component configuration on the selected test definition."),
    Action("server-definitions", "Discover server metrics", "server",
           "Uses Azure Monitor to discover metrics and valid aggregations on this application resource."),
    Action("attach-server-metric", "Attach server metric to test", "server",
           "Registers a discovered metric definition on the test; configure this before starting a run.",
           "This updates server-metric configuration on the selected test definition."),
    Action("server-metrics", "Get server metric values", "server",
           "Queries Azure Monitor on the application resource using the run's start and end times."),
    Action("resolve-load-endpoint", "Resolve Load Testing endpoint", "insights",
           "Reads the selected resource's data-plane endpoint from Azure Resource Manager. Creates nothing."),
    Action("ai-insights", "Get latest AI insights", "insights",
           "Reads existing AI-generated insights for the selected completed run via REST. Does not generate insights or start traffic."),
    Action("download-logs", "Download engine logs", "downloads",
           "Downloads engine log ZIPs. These are different from application/server logs."),
    Action("download-results", "Download request results", "downloads",
           "Downloads raw request results for detailed JMeter/Locust analysis."),
    Action("download-report", "Download JMeter HTML report", "downloads",
           "Downloads the report archive when available. Not every run/engine has this artifact."),
    Action("collect-evidence", "Collect analysis bundle", "downloads",
           "Downloads run metadata, logs, results, and client/engine metrics. Adds server values if configured."),
)
ACTION_BY_ID = {action.id: action for action in ACTIONS}


@dataclass(frozen=True)
class Config:
    tenant: str = ""
    subscription: str = ""
    resourceGroup: str = ""
    loadTestResource: str = ""
    loadTestEndpoint: str = ""
    testId: str = "cli-demo-smoke"
    runId: str = ""
    targetUrl: str = ""
    testListOutput: str = "json"
    clientMetric: str = "ResponseTime"
    engineMetric: str = "CPU"
    serverResourceId: str = ""
    serverResourceName: str = ""
    serverResourceType: str = ""
    serverResourceKind: str = ""
    serverMetric: str = ""
    serverMetricId: str = ""
    serverNamespace: str = ""
    serverAggregation: str = "Average"
    startTime: str = ""
    endTime: str = ""

    @classmethod
    def parse(cls, value: Any) -> Config:
        if not isinstance(value, dict):
            raise ValueError("Configuration must be a JSON object.")
        unknown = value.keys() - cls.__dataclass_fields__.keys()
        if unknown:
            raise ValueError(f"Unknown configuration fields: {', '.join(sorted(unknown))}")
        for key, item in value.items():
            if not isinstance(item, str) or len(item) > 2048:
                raise ValueError(f"{key} must be a string of at most 2048 characters.")
            if any(ord(char) < 32 for char in item):
                raise ValueError(f"{key} must not contain control characters.")
            if item.strip().startswith("-"):
                raise ValueError(f"{key} must not be an option.")
        config = cls(**{key: item.strip() for key, item in value.items()})
        if config.testListOutput not in {"json", "tsv"}:
            raise ValueError("Choose json or tsv for testListOutput.")
        return config

    def require(self, *names: str) -> None:
        for name in names:
            if not getattr(self, name):
                raise ValueError(f"Configure {name} before using this command.")

    def load_resource_id(self) -> str:
        self.require("subscription", "resourceGroup", "loadTestResource")
        if not GUID.fullmatch(self.subscription):
            raise ValueError("Use a subscription GUID, not a display name.")
        for name in ("resourceGroup", "loadTestResource"):
            if not RESOURCE_NAME.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} contains an invalid resource name.")
        return (f"/subscriptions/{self.subscription}/resourceGroups/{self.resourceGroup}"
                f"/providers/Microsoft.LoadTestService/loadTests/{self.loadTestResource}")

    def load_scope(self) -> list[str]:
        return ["--subscription", self.subscription, "--resource-group", self.resourceGroup,
                "--load-test-resource", self.load_resource_id()]

    def insights_url(self) -> str:
        self.run_scope()
        self.require("loadTestEndpoint")
        endpoint = load_endpoint(self.loadTestEndpoint)
        return f"{endpoint}/test-runs/{self.runId}/insights/latest?api-version={INSIGHTS_API_VERSION}"

    def test_scope(self) -> list[str]:
        self.require("testId")
        if not IDENTIFIER.fullmatch(self.testId):
            raise ValueError("testId must be 1-50 letters, digits, underscores or hyphens.")
        return ["--test-id", self.testId]

    def run_scope(self) -> list[str]:
        self.require("runId")
        if not IDENTIFIER.fullmatch(self.runId):
            raise ValueError("runId must be 1-50 letters, digits, underscores or hyphens.")
        return ["--test-run-id", self.runId]

    def server_scope(self) -> list[str]:
        self.require("subscription", "serverResourceId")
        if not GUID.fullmatch(self.subscription):
            raise ValueError("Use a subscription GUID, not a display name.")
        parts = self.serverResourceId.strip("/").split("/")
        if (len(parts) < 8 or len(parts) % 2 or parts[0].lower() != "subscriptions"
                or parts[2].lower() != "resourcegroups" or parts[4].lower() != "providers"
                or any(not part or part in {".", ".."} for part in parts)):
            raise ValueError("serverResourceId must be a complete Azure ARM resource ID.")
        if parts[1].lower() != self.subscription.lower():
            raise ValueError("For this demo, select a server resource in the configured subscription.")
        return ["--subscription", self.subscription, "--resource", self.serverResourceId]

    def time_scope(self) -> list[str]:
        self.require("startTime", "endTime")
        try:
            start = datetime.fromisoformat(self.startTime.replace("Z", "+00:00"))
            end = datetime.fromisoformat(self.endTime.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Use ISO 8601 timestamps, for example 2026-10-19T13:00:00Z.") from exc
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("Run timestamps must include a timezone and end after start.")
        return ["--start-time", self.startTime, "--end-time", self.endTime]


@dataclass(frozen=True)
class Command:
    args: tuple[str, ...]
    artifact: str = ""
    metric: str = ""
    namespace: str = ""
    download: bool = False

    def public(self) -> dict[str, Any]:
        return {"argv": ["az", *self.args], "powershell": shell_command(self.args),
                "bash": shell_command(self.args, "bash"), "artifact": self.artifact}


def shell_command(args: tuple[str, ...] | list[str], shell: str = "powershell") -> str:
    tokens = ["az", *args]
    if shell == "bash":
        for option in ("--load-test-config-file", "--path"):
            if option in tokens:
                index = tokens.index(option) + 1
                tokens[index] = Path(tokens[index]).as_posix()
        return shlex.join(tokens)
    def quote(token: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_./:=@-]+", token):
            return token
        return "'" + token.replace("'", "''") + "'"
    return " ".join(quote(token) for token in tokens)


def script_for(commands: list[Command], shell: str = "powershell",
               test_config: dict[str, str] | None = None) -> str:
    lines = ["# Generated from the app's allowlisted command preview.",
             "# Run from the root of a checkout of san360/contoso-bank-loadtest.",
             "# Uses your existing az login session. Review the target and cost before execution.",
             "# The load extension may require the active CLI subscription to match --subscription.",
             "# Select it explicitly with az account set, preferably in a separate AZURE_CONFIG_DIR."]
    lines.append("set -euo pipefail" if shell == "bash" else "$ErrorActionPreference = 'Stop'")
    portable_config = None
    if test_config is not None:
        portable_config = Path(".loadtest-demo") / "artifacts" / Path(test_config["path"]).parent.name / "smoke.yaml"
        template = Path("loadtests") / "cli-demo" / "smoke.jmx"
        content = test_config["content"]
        if shell == "bash":
            target = shlex.quote(portable_config.as_posix())
            source = shlex.quote(template.as_posix())
            lines.extend([
                "",
                f'test -f {source} || {{ printf "%s\\n" "Run this script from the repository root." >&2; exit 1; }}',
                f"mkdir -p {shlex.quote(portable_config.parent.as_posix())}",
                f"cp {source} {shlex.quote(portable_config.with_suffix('.jmx').as_posix())}",
                f"cat > {target} <<'AZURE_LOAD_TEST_DEMO_YAML'",
                content.rstrip("\n"),
                "AZURE_LOAD_TEST_DEMO_YAML",
            ])
        else:
            target = str(portable_config).replace("'", "''")
            source = str(template).replace("'", "''")
            parent = str(portable_config.parent).replace("'", "''")
            jmx = str(portable_config.with_suffix(".jmx")).replace("'", "''")
            yaml_literal = content.replace("'", "''")
            lines.extend([
                "",
                f"if (-not (Test-Path -LiteralPath '{source}')) {{ throw 'Run this script from the repository root.' }}",
                f"New-Item -ItemType Directory -Force -Path '{parent}' | Out-Null",
                f"Copy-Item -LiteralPath '{source}' -Destination '{jmx}' -Force",
                f"[System.IO.File]::WriteAllText((Join-Path $PWD.Path '{target}'), '{yaml_literal}', [System.Text.UTF8Encoding]::new($false))",
            ])
    for command in commands:
        args = list(command.args)
        if portable_config is not None and "--load-test-config-file" in args:
            args[args.index("--load-test-config-file") + 1] = (
                portable_config.as_posix() if shell == "bash" else str(portable_config))
        lines.extend(["", shell_command(args, shell)])
        if shell != "bash":
            lines.append("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }")
    return "\n".join(lines) + "\n"


def target_environment(target: str) -> list[str]:
    try:
        url = urlsplit(target)
        port = url.port or (443 if url.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("targetUrl has an invalid host or port.") from exc
    if (url.scheme not in {"https", "http"} or not url.hostname or url.username
            or url.password or url.fragment or not 1 <= port <= 65535):
        raise ValueError("targetUrl must be an HTTP(S) URL without credentials or a fragment.")
    if url.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Azure engines cannot reach localhost. Use an authorized, reachable target.")
    if any(SECRET_KEY.search(key) for key, _ in parse_qsl(url.query)):
        raise ValueError("Do not put credentials or tokens in the demo target URL.")
    path = url.path or "/"
    if url.query:
        path += "?" + url.query
    return ["--env", f"DEMO_HOST={url.hostname}", f"DEMO_PROTOCOL={url.scheme}",
            f"DEMO_PORT={port}", f"DEMO_PATH={path}"]


def render_test_config(template: str, config: Config) -> str:
    content, count = re.subn(r"(?m)^testId:.*$",
                            lambda _: f"testId: {json.dumps(config.testId)}", template)
    if count != 1:
        raise ValueError("The demo YAML must define exactly one top-level testId.")
    for assignment in target_environment(config.targetUrl)[1:]:
        name, value = assignment.split("=", 1)
        pattern = rf"(?m)^(  - name: {name}\r?\n    value:)[^\r\n]*"
        content, count = re.subn(pattern, lambda match: f"{match[1]} {json.dumps(value)}", content)
        if count != 1:
            raise ValueError(f"The demo YAML must define exactly one env value for {name}.")
    return content


def load_endpoint(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Azure did not return a Load Testing data-plane endpoint. Resolve the resource again.")
    if any(ord(char) < 33 for char in value):
        raise ValueError("The Load Testing endpoint must not contain whitespace or control characters.")
    try:
        url = urlsplit(value if "://" in value else "https://" + value)
        if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
                or url.port is not None or url.path not in {"", "/"} or url.query or url.fragment
                or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.cnt-prod\.loadtesting\.azure\.com",
                                    url.hostname)):
            raise ValueError("Unexpected endpoint.")
    except ValueError as exc:
        raise ValueError("Use the Azure public-cloud Load Testing endpoint returned by resource discovery, "
                         "without a custom port, path, query or credentials.") from exc
    return f"https://{url.hostname}"


def insights_view(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or "error" in payload:
        raise ValueError("The insights API did not return a TestRunInsights object. Inspect the response JSON.")
    status = payload.get("status", "Unknown")
    if not isinstance(status, str) or not status:
        raise ValueError("The insights response status must be a non-empty string.")
    version = payload.get("version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        raise ValueError("The insights response version must be an integer.")
    columns = payload.get("columns", [])
    if not isinstance(columns, list) or any(
        not isinstance(column, dict) or not isinstance(column.get("name"), str) or not column["name"]
        or not isinstance(column.get("dataType"), str) for column in columns
    ):
        raise ValueError("The insights response columns must contain name/dataType definitions.")
    names = list(dict.fromkeys(column["name"] for column in columns))
    rows = payload.get("rows", {})
    if not isinstance(rows, dict):
        raise ValueError("The insights response rows must be keyed objects for API version "
                         f"{INSIGHTS_API_VERSION}. Inspect the response JSON.")
    normalized = []
    for id_, values in rows.items():
        if not isinstance(values, dict) or any(not isinstance(value, str) for value in values.values()):
            raise ValueError("Each insight row must map column names to string values.")
        normalized.append({"id": id_, "values": values})
        for name in values:
            if name not in names:
                names.append(name)
    return {"status": status, "version": version, "columns": names, "rows": normalized}


def build_commands(action_id: str, config: Config, artifact_dir: Path) -> list[Command]:
    if action_id not in ACTION_BY_ID:
        raise ValueError("Unknown demo action. Arbitrary commands are not supported.")
    def command(args: list[str], artifact: str = "", metric: str = "",
                namespace: str = "", download: bool = False) -> Command:
        output = "tsv" if artifact.endswith(".tsv") else "json"
        return Command(tuple([*args, "--only-show-errors", "--output", output]),
                       artifact, metric, namespace, download)

    if action_id == "subscriptions":
        return [command(["account", "list", "--query",
                         "[?state=='Enabled'].{id:id,name:name,tenantId:tenantId}"], "subscriptions.json")]
    if action_id == "account":
        args = ["account", "show", "--query", "{id:id,name:name,tenantId:tenantId,state:state}"]
        return [command(args, "account.json")]
    if action_id == "select-subscription":
        config.require("subscription")
        if not GUID.fullmatch(config.subscription):
            raise ValueError("Use a subscription GUID, not a display name.")
        return [command(["account", "set", "--subscription", config.subscription], "context.json")]
    if action_id == "resources":
        config.require("subscription")
        if not GUID.fullmatch(config.subscription):
            raise ValueError("Use a subscription GUID, not a display name.")
        return [command(["resource", "list", "--subscription", config.subscription,
                         "--resource-type", "Microsoft.LoadTestService/loadtests",
                         "--query", "[].{name:name,resourceGroup:resourceGroup,id:id}"],
                        "resources.json")]
    if action_id in {"component-details", "server-definitions", "server-metrics"}:
        scope = config.server_scope()
        if action_id == "component-details":
            return [command(["resource", "show", "--subscription", config.subscription,
                             "--ids", config.serverResourceId,
                             "--query", "{id:id,name:name,type:type,kind:kind}"], "component.json")]
        if action_id == "server-definitions":
            return [command(["monitor", "metrics", "list-definitions", *scope],
                            "server-definitions.json")]
        config.require("serverMetric")
        if config.serverAggregation not in {"Average", "Minimum", "Maximum", "Total", "Count"}:
            raise ValueError("Choose an Azure Monitor aggregation from the list.")
        args = ["monitor", "metrics", "list", *scope, "--metrics", config.serverMetric,
                "--aggregation", config.serverAggregation, "--interval", "PT1M",
                *config.time_scope()]
        if config.serverNamespace:
            args += ["--namespace", config.serverNamespace]
        return [command(args, "server-metrics.json", config.serverMetric, config.serverNamespace)]

    scope = config.load_scope()

    if action_id == "resolve-load-endpoint":
        return [command(["resource", "show", "--subscription", config.subscription,
                         "--ids", config.load_resource_id(),
                         "--query", "{id:id,dataPlaneURI:properties.dataPlaneURI}"], "load-resource.json")]
    if action_id == "ai-insights":
        config.test_scope()
        return [
            *build_commands("show-run", config, artifact_dir),
            command(["rest", "--method", "get", "--url", config.insights_url(),
                     "--resource", LOAD_TEST_AUDIENCE, "--subscription", config.subscription],
                    "ai-insights.json"),
        ]

    def load(args: list[str], artifact: str = "", metric: str = "",
             namespace: str = "", download: bool = False) -> Command:
        return command(["load", *args, *scope], artifact, metric, namespace, download)

    def metrics(namespace: str, metric: str, artifact: str) -> Command:
        if not metric or len(metric) > 128 or metric.startswith("-"):
            raise ValueError("Choose a metric name from the discovered definitions.")
        return load(["test-run", "metrics", "list", *config.run_scope(),
                     "--metric-namespace", namespace, "--metric-name", metric, "--interval", "PT1M"],
                    artifact, metric, namespace)

    def download(flag: str) -> Command:
        return load(["test-run", "download-files", *config.run_scope(),
                     "--path", str(artifact_dir / "files"), "--force", flag], download=True)

    if action_id == "list-tests":
        if config.testListOutput == "tsv":
            return [load(["test", "list", "--query",
                          "[].[testId,displayName,testType,loadTestConfiguration.engineInstances]"],
                         "tests.tsv")]
        if config.testListOutput != "json":
            raise ValueError("Choose json or tsv for testListOutput.")
        return [load(["test", "list"], "tests.json")]
    if action_id == "create-test":
        config.require("targetUrl")
        config_file = artifact_dir / "smoke.yaml"
        if config_file.is_absolute() and config_file.is_relative_to(ROOT):
            config_file = config_file.relative_to(ROOT)
        return [load(["test", "create", *config.test_scope(), "--load-test-config-file",
                      str(config_file),
                      *target_environment(config.targetUrl)], "test.json")]
    if action_id == "show-test":
        return [load(["test", "show", *config.test_scope()], "test.json")]
    if action_id == "run-test":
        return [load(["test-run", "create", *config.test_scope(), *config.run_scope(), "--no-wait"],
                     "run-start.json")]
    if action_id == "list-runs":
        return [load(["test-run", "list", *config.test_scope()], "runs.json")]
    if action_id == "show-run":
        return [load(["test-run", "show", *config.run_scope()], "run.json")]
    if action_id == "stop-run":
        return [load(["test-run", "stop", *config.run_scope(), "--yes"], "run-stop.json")]
    if action_id == "namespaces":
        return [load(["test-run", "metrics", "get-namespaces", *config.run_scope()],
                     "namespaces.json")]
    if action_id == "client-definitions":
        return [load(["test-run", "metrics", "get-definitions", *config.run_scope(),
                      "--metric-namespace", "LoadTestRunMetrics"], "client-definitions.json")]
    if action_id == "all-client-metrics":
        config.test_scope()
        return [
            *build_commands("show-run", config, artifact_dir),
            *build_commands("client-definitions", config, artifact_dir),
            load(["test-run", "metrics", "list", *config.run_scope(),
                  "--metric-namespace", "LoadTestRunMetrics", "--interval", "PT1M"],
                 "all-client-metrics.json", namespace="LoadTestRunMetrics"),
        ]
    if action_id == "client-metrics":
        return [metrics("LoadTestRunMetrics", config.clientMetric, "client-metrics.json")]
    if action_id == "engine-metrics":
        return [metrics("EngineHealthMetrics", config.engineMetric, "engine-metrics.json")]
    if action_id == "run-components":
        return [load(["test-run", "app-component", "list", *config.run_scope()],
                     "app-components.json")]
    if action_id == "run-server-config":
        return [load(["test-run", "server-metric", "list", *config.run_scope()],
                     "server-metric-config.json")]
    if action_id in {"attach-component", "attach-server-metric"}:
        config.server_scope()
        config.require("serverResourceName", "serverResourceType")
        args = ["test", "app-component", "add", *config.test_scope(),
                "--app-component-id", config.serverResourceId,
                "--app-component-name", config.serverResourceName,
                "--app-component-type", config.serverResourceType]
        if config.serverResourceKind:
            args += ["--app-component-kind", config.serverResourceKind]
        if action_id == "attach-server-metric":
            config.require("serverMetric", "serverMetricId", "serverNamespace")
            if not config.serverMetricId.lower().startswith(config.serverResourceId.lower() + "/"):
                raise ValueError("Select a metric definition discovered on this application resource.")
            if config.serverAggregation not in {"Average", "Minimum", "Maximum", "Total", "Count"}:
                raise ValueError("Choose an Azure Monitor aggregation from the list.")
            args = ["test", "server-metric", "add", *config.test_scope(),
                    "--app-component-id", config.serverResourceId,
                    "--app-component-type", config.serverResourceType,
                    "--metric-id", config.serverMetricId, "--metric-name", config.serverMetric,
                    "--metric-namespace", config.serverNamespace,
                    "--aggregation", config.serverAggregation]
        return [load(args, "component-config.json" if action_id == "attach-component"
                     else "server-metric-config.json")]
    if action_id in {"download-logs", "download-results", "download-report"}:
        return [download({"download-logs": "--log", "download-results": "--result",
                          "download-report": "--report"}[action_id])]
    if action_id == "collect-evidence":
        commands = [load(["test-run", "show", *config.run_scope()], "run.json"),
                    download("--log"), download("--result"),
                    metrics("LoadTestRunMetrics", config.clientMetric, "client-metrics.json"),
                    metrics("EngineHealthMetrics", config.engineMetric, "engine-metrics.json")]
        if config.serverResourceId and config.serverMetric:
            commands += build_commands("server-metrics", config, artifact_dir)
        return commands
    raise ValueError(f"No command builder for {action_id}.")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SECRET_KEY.search(key) else redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(https?://[^\s?\"'<>]+)\?[^\s\"'<>]*"
                       r"(?:sig|token|key|secret|password)=[^\s\"'<>]*",
                       r"\1?[REDACTED]", value)
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
        value = re.sub(r"""(?i)(["']?(?:password|secret|token|api[-_]?key|authorization|cookie|AccountKey)
                       ["']?\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)""",
                       r"\1[REDACTED]", value, flags=re.VERBOSE)
        return value
    return value


def run_verdict(run: dict[str, Any]) -> dict[str, str]:
    status = str(run.get("status", "UNKNOWN")).upper()
    service_result = str(run.get("testResult", "UNKNOWN")).upper()
    if status in {"FAILED", "CANCELLED", "CANCELED", "STOPPED", "AUTOSTOPPED"}:
        return {"label": status, "kind": "bad", "detail": "Execution did not complete normally."}
    if status != "DONE":
        return {"label": status, "kind": "pending", "detail": "Execution is not complete."}
    criteria = run.get("passFailCriteria") or {}
    values = []
    for group in ("passFailMetrics", "passFailServerMetrics"):
        entries = criteria.get(group) or {}
        values.extend(entries.values() if isinstance(entries, dict) else entries)
    results = [str(item.get("result", "")).upper() for item in values if isinstance(item, dict)]
    if "FAILED" in results or service_result == "FAILED":
        return {"label": "FAILED", "kind": "bad", "detail": "At least one fail criterion was breached."}
    if not values:
        return {"label": "NO VERDICT", "kind": "warning",
                "detail": "DONE without evaluated fail criteria is not evidence of a pass."}
    if len(results) != len(values) or any(result != "PASSED" for result in results):
        return {"label": "SETTLING", "kind": "pending",
                "detail": "Criteria have not all settled. Refresh; do not report a pass yet."}
    if service_result != "PASSED":
        return {"label": "SETTLING", "kind": "pending",
                "detail": "Criteria passed but the aggregate verdict is not settled. Refresh."}
    return {"label": "PASSED", "kind": "good", "detail": "Execution and all fail criteria passed."}


def metric_rows(payload: Any, metric: str = "", namespace: str = "",
                definitions: Any = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    definition_list = definitions.get("value", []) if isinstance(definitions, dict) else definitions or []
    metadata = {}
    for definition in definition_list:
        if isinstance(definition, dict):
            name = definition.get("name")
            if isinstance(name, dict):
                name = name.get("value")
            if isinstance(name, str):
                metadata[name] = definition

    def context_for(name: str) -> dict[str, Any]:
        definition = metadata.get(name, {})
        return {"metric": name, "namespace": definition.get("namespace") or namespace,
                "unit": definition.get("unit") or "", "dimensions": ""}

    def visit(node: Any, context: dict[str, Any]) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, context)
            return
        if not isinstance(node, dict):
            return
        context = dict(context)
        name = node.get("name")
        if isinstance(name, dict):
            name = name.get("value")
        context["metric"] = node.get("metricName") or name or context["metric"]
        context["unit"] = node.get("unit", context["unit"])
        context["namespace"] = node.get("namespace", context["namespace"])
        dimensions = node.get("metadatavalues") or node.get("metadataValues") or node.get("dimensions")
        if dimensions:
            context["dimensions"] = json.dumps(redact(dimensions), sort_keys=True)
        timestamp = node.get("timeStamp") or node.get("timestamp")
        if timestamp:
            for aggregation, value in node.items():
                if aggregation.lower() in {"value", "average", "total", "count", "minimum", "maximum"}:
                    if value is not None:
                        rows.append({**context, "timestamp": timestamp,
                                     "aggregation": aggregation, "value": value})
        for key in ("value", "timeseries", "timeSeries", "data"):
            if isinstance(node.get(key), (list, dict)):
                visit(node[key], context)

    if (not metric and namespace and isinstance(payload, dict)
            and not any(key in payload for key in ("value", "timeseries", "timeSeries", "data"))):
        for name, series in payload.items():
            visit(series, context_for(name))
    else:
        visit(payload, context_for(metric))
    return rows


def _spreadsheet_value(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def parse_test_list_tsv(stdout: str) -> tuple[dict[str, Any], str]:
    try:
        rows = list(csv.reader(io.StringIO(stdout), delimiter="\t", quoting=csv.QUOTE_NONE))
    except csv.Error as exc:
        raise ValueError("Could not read Azure CLI TSV output. Select JSON and inspect raw stdout below.") from exc
    if any(len(row) != len(TEST_LIST_COLUMNS) for row in rows):
        raise ValueError("Azure CLI returned unexpected TSV columns. Select JSON for definitions "
                         "containing embedded tabs or newlines; inspect raw stdout below.")
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t")
    writer.writerow(TEST_LIST_COLUMNS)
    writer.writerows([_spreadsheet_value(value) for value in row] for row in rows)
    return {"headers": list(TEST_LIST_COLUMNS), "rows": rows}, output.getvalue()


def _delimited_export(payload: Any, metric: str, namespace: str, definitions: Any, delimiter: str) -> str:
    output = io.StringIO(newline="")
    columns = ["timestamp", "namespace", "metric", "unit", "dimensions", "aggregation", "value"]
    writer = csv.DictWriter(output, fieldnames=columns, delimiter=delimiter)
    writer.writeheader()
    for row in metric_rows(payload, metric, namespace, definitions):
        # Downloads can be opened in Excel; do not allow a metric/dimension to become a formula.
        writer.writerow({key: _spreadsheet_value(value) for key, value in row.items()})
    return output.getvalue()


def csv_export(payload: Any, metric: str = "", namespace: str = "", definitions: Any = None) -> str:
    return _delimited_export(payload, metric, namespace, definitions, ",")


def tsv_export(payload: Any, metric: str = "", namespace: str = "", definitions: Any = None) -> str:
    return _delimited_export(payload, metric, namespace, definitions, "\t")


def public_actions() -> list[dict[str, str]]:
    return [asdict(action) for action in ACTIONS]
