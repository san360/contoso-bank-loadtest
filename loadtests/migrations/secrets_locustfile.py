"""Migration of contoso-bank-secrets.jmx without evaluating its embedded scripts."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from time import perf_counter

from locust import events, task
from locust.env import Environment
from locust.exception import StopUser

from jmeter_runtime import ScheduledHttpUser, ScheduledRun, startup_failure

TIMEOUT = (10, 30)
JAVA_TRIM_CHARACTERS = "".join(chr(number) for number in range(33))


@dataclass(frozen=True)
class SecretsRuntime:
    run: ScheduledRun
    account_id: str


runtime: SecretsRuntime | None = None


@events.test_start.add_listener
def initialize(environment: Environment, **_kwargs: object) -> None:
    global runtime
    runtime = None
    try:
        if os.environ.get("DEMO_LOG_SECRET", "").lower() == "true":
            raise ValueError("Raw secret logging is intentionally unsupported; use false.")
        account_id = os.environ.get("ACCOUNT_ID", "ACC-1001")
        if not account_id.strip():
            raise ValueError("ACCOUNT_ID must not be empty.")
        runtime = SecretsRuntime(ScheduledRun.start(environment), account_id)
    except ValueError as exc:
        startup_failure(environment, str(exc))


def secret_is_resolved(value: str | None) -> bool:
    return (
        value is not None
        and bool(value.strip(JAVA_TRIM_CHARACTERS))
        and "${" not in value
        and value != "API_KEY"
    )


class SecretsDemoCustomer(ScheduledHttpUser):
    def on_start(self) -> None:
        if runtime is None:
            logging.error("Secrets migration has no initialized runtime; no HTTP will be sent.")
            raise StopUser()
        self.state = runtime
        self.schedule = self.state.run
        self.configure_session()
        self.disable_cookies()
        started = perf_counter()
        api_key = os.environ.get("API_KEY")
        resolved = secret_is_resolved(api_key)
        if resolved and api_key is not None:
            length = len(api_key.encode("utf-16-le")) // 2
            fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
            message = (
                f"Key Vault secret API_KEY resolved. length={length} "
                f"sha256_prefix={fingerprint}"
            )
            exception = None
        else:
            message = (
                "Key Vault secret API_KEY was NOT resolved. Check its secret reference, "
                "reference identity and Key Vault Secrets User permission."
            )
            exception = ValueError(message)
        self.environment.events.request.fire(
            request_type="JSR223",
            name="SecretResolutionCheck",
            response_time=(perf_counter() - started) * 1000,
            response_length=len(message.encode("utf-8")),
            exception=exception,
            context={},
        )
        if not resolved or api_key is None:
            logging.error("%s", message)
            self.environment.process_exit_code = 1
            raise StopUser()
        logging.warning("%s", message)
        self.client.headers.update(
            {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
        )

    def get_asserted(self, path: str, name: str, failure_message: str) -> None:
        self.schedule.before_sample()
        with self.client.get(
            path,
            name=name,
            timeout=TIMEOUT,
            allow_redirects=True,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(failure_message)

    @task
    def banking_iteration(self) -> None:
        self.reset_connections()
        self.get_asserted(
            f"/api/accounts/{self.state.account_id}/balance",
            "GetBalanceWithSecretHeader",
            "Balance request did not return 200",
        )
        self.get_asserted(
            "/health",
            "HealthCheckWithSecretHeader",
            "Health request did not return 200",
        )
