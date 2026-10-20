"""Fixed resource, workload and metric boundaries for the disposable demo."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from tools.gitlab_demo.common import DemoError, ROOT, read_json

INFRA = ROOT / "infra" / "sql-metrics-demo"
LOAD = ROOT / "loadtests" / "sql-metrics-demo"
JDBC_NAME = "mssql-jdbc-13.6.1.jre11.jar"
JDBC_URL = f"https://repo.maven.apache.org/maven2/com/microsoft/sqlserver/mssql-jdbc/13.6.1.jre11/{JDBC_NAME}"
JDBC_SHA256 = "4f470d23dfdf187ece2d1f9feb33d9dbd2230051b9c9676456727692a1b97d5e"
NAMESPACE = "Microsoft.Sql/servers/databases"
METRICS = {
    "cpu_percent": "Average",
    "dtu_consumption_percent": "Average",
    "physical_data_read_percent": "Average",
    "log_write_percent": "Average",
    "workers_percent": "Average",
    "sessions_percent": "Average",
    "sessions_count": "Average",
    "connection_successful": "Total",
}
ALT_ID = re.compile(
    r"/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/"
    r"Microsoft\.LoadTestService/loadTests/([^/]+)", re.IGNORECASE,
)


@dataclass(frozen=True)
class Context:
    subscription_id: str
    tenant_id: str
    demo_name: str
    location: str
    load_test_resource_id: str
    entra_admin_object_id: str
    load_test_principal_id: str
    load_test_client_id: str

    def __post_init__(self) -> None:
        for field in ("subscription_id", "tenant_id", "entra_admin_object_id", "load_test_principal_id", "load_test_client_id"):
            if str(UUID(getattr(self, field))) != getattr(self, field).lower():
                raise DemoError(f"{field} must be a GUID")
        match = ALT_ID.fullmatch(self.load_test_resource_id)
        if not match or match[1].lower() != self.subscription_id.lower():
            raise DemoError("Use an ALT ARM ID in the explicitly selected subscription")
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,19}", self.demo_name):
            raise DemoError("Demo name must be 3-20 lowercase letters, digits or hyphens")
        if not re.fullmatch(r"[a-z0-9]+", self.location):
            raise DemoError("Use a canonical Azure region name")

    @property
    def token(self) -> str:
        return hashlib.sha256(
            f"{self.subscription_id}/{self.load_test_resource_id.lower()}/{self.demo_name}".encode()
        ).hexdigest()[:10]

    @property
    def group_name(self) -> str:
        return f"rg-sqlmetrics-{self.demo_name}-{self.token[:6]}"

    @property
    def group_id(self) -> str:
        return f"/subscriptions/{self.subscription_id}/resourceGroups/{self.group_name}"

    @property
    def server_name(self) -> str:
        return f"sql-metrics-{self.token}"

    @property
    def server_id(self) -> str:
        return f"{self.group_id}/providers/Microsoft.Sql/servers/{self.server_name}"

    @property
    def database_id(self) -> str:
        return f"{self.server_id}/databases/metrics-demo"

    @property
    def subnet_id(self) -> str:
        return f"{self.group_id}/providers/Microsoft.Network/virtualNetworks/vnet-sqlmetrics/subnets/load-engines"

    @property
    def host(self) -> str:
        return f"{self.server_name}.database.windows.net"

    @property
    def test_id(self) -> str:
        return f"sql-metrics-{self.demo_name}-{self.token[:6]}"

    @property
    def marker(self) -> str:
        return f"Isolated SQL metrics demo {self.token}; owned by tools.sql_metrics_demo"

    def tfvars(self) -> dict:
        return {
            name: getattr(self, name) for name in (
                "subscription_id", "tenant_id", "demo_name", "location",
                "load_test_resource_id", "entra_admin_object_id",
            )
        }


def test_configuration(context: Context) -> dict:
    payload = read_json(LOAD / "test.json")
    payload.update({
        "description": context.marker,
        "subnetId": context.subnet_id,
        "environmentVariables": {"SQL_HOST": context.host, "SQL_DATABASE": "metrics-demo"},
    })
    return payload


def selected_metrics(definitions: list[dict], context: Context) -> dict:
    by_name = {item["name"]["value"]: item for item in definitions}
    result = {}
    for name, aggregation in METRICS.items():
        definition = by_name.get(name)
        if (not definition or aggregation not in definition.get("supportedAggregationTypes", [])
                or definition.get("namespace", "").lower() != NAMESPACE.lower()
                or not definition.get("id", "").lower().startswith(context.database_id.lower() + "/")):
            raise DemoError(f"Selected Basic database does not expose {name}/{aggregation}")
        result[definition["id"]] = {
            "resourceId": context.database_id, "resourceType": NAMESPACE,
            "metricNamespace": definition["namespace"], "name": name,
            "aggregation": aggregation,
        }
    return result


def metric_configuration_patch(current: dict, selected: dict, context: Context) -> dict:
    existing = current.get("metrics", {})
    if any(item.get("resourceId", "").lower() != context.database_id.lower() for item in existing.values()):
        raise DemoError("Refusing to replace a foreign resource's metric configuration")
    return {"metrics": {**{key: None for key in existing if key not in selected}, **selected}}


def check_plan(plan: dict, context: Context, *, destroy: bool = False) -> None:
    expected = {
        "azurerm_resource_group.demo": context.group_id,
        "azurerm_virtual_network.demo": context.subnet_id.rsplit("/subnets/", 1)[0],
        "azurerm_subnet.engines": context.subnet_id,
        "azurerm_mssql_server.demo": context.server_id,
        "azurerm_mssql_database.demo": context.database_id,
        "azurerm_subnet.private_endpoint": context.subnet_id.rsplit("/", 1)[0] + "/sql-private-endpoint",
        "azurerm_private_endpoint.sql": context.group_id + "/providers/Microsoft.Network/privateEndpoints/sql-metrics",
        "azurerm_private_dns_zone.sql": context.group_id + "/providers/Microsoft.Network/privateDnsZones/privatelink.database.windows.net",
        "azurerm_private_dns_zone_virtual_network_link.demo": context.group_id + "/providers/Microsoft.Network/privateDnsZones/privatelink.database.windows.net/virtualNetworkLinks/only-demo-vnet",
        "azurerm_role_assignment.metrics_reader": context.database_id + "/providers/Microsoft.Authorization/roleAssignments/",
    }
    for change in plan.get("resource_changes", []):
        actions = change["change"]["actions"]
        if change.get("mode") == "data":
            if change["address"] != "data.azurerm_load_test.existing" or actions not in (["read"], ["no-op"]):
                raise DemoError("Plan contains an unexpected data source")
            continue
        address = change["address"]
        if address not in expected or actions not in (["no-op"], ["delete"] if destroy else ["create"]):
            raise DemoError(f"Refusing non-demo or non-additive Terraform action: {address} {actions}")
        before = change["change"].get("before")
        if before:
            resource_id = before.get("id", "").lower()
            expected_id = expected[address].lower()
            matches = resource_id.startswith(expected_id) if address.endswith("metrics_reader") else resource_id == expected_id
            if not matches:
                raise DemoError(f"Terraform state contains a foreign resource: {address}")
    if not isinstance(plan.get("resource_changes"), list):
        raise DemoError("Terraform plan did not describe its resources")


def state_directory(value: Path) -> Path:
    folder = value.resolve()
    allowed = (ROOT / ".loadtest-demo" / "sql-metrics").resolve()
    if not folder.is_relative_to(allowed) or folder == allowed:
        raise DemoError("Use a named state folder under .loadtest-demo/sql-metrics/")
    return folder
