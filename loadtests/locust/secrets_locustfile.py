"""Locust migration of ../jmeter/contoso-bank-secrets.jmx.

Azure Load Testing resolves the YAML's Key Vault reference into API_KEY.
Local runs must supply API_KEY through the environment instead.
"""

import hashlib
import logging
import os
from time import perf_counter

from locust import HttpUser, constant, events, task
from locust.exception import StopUser


class KeyVaultSecretsCustomer(HttpUser):
    wait_time = constant(0)
    _demo_secret_logged = False

    def on_start(self):
        started = perf_counter()
        api_key = os.getenv("API_KEY")
        if (
            api_key is None
            or not api_key.strip()
            or "${" in api_key
            or api_key == "API_KEY"
            or "\r" in api_key
            or "\n" in api_key
        ):
            message = (
                "API_KEY was not resolved into a non-empty, usable secret. "
                "Check the API_KEY secret parameter, Key Vault secret URI, "
                "reference identity, Key Vault Secrets User role and vault networking. "
                "Local runs must supply API_KEY as an environment variable."
            )
            logging.error(message)
            self._record_secret_check(started, message, RuntimeError(message))
            self.environment.process_exit_code = 1
            raise StopUser()

        fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
        message = (
            f"Key Vault secret API_KEY resolved. length={len(api_key)} "
            f"sha256_prefix={fingerprint}"
        )
        logging.warning(message)
        if (
            os.getenv("DEMO_LOG_SECRET", "").lower() == "true"
            and not KeyVaultSecretsCustomer._demo_secret_logged
        ):
            logging.warning("DEMO ONLY: retrieved API_KEY=%s", api_key)
            KeyVaultSecretsCustomer._demo_secret_logged = True

        self._record_secret_check(started, message)
        self.account_id = os.getenv("accountId", "ACC-1001")
        self.client.headers.update(
            {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
        )

    def _record_secret_check(self, started, message, exception=None):
        self.environment.events.request.fire(
            request_type="SECRET",
            name="SecretResolutionCheck",
            response_time=(perf_counter() - started) * 1000,
            response_length=len(message.encode("utf-8")),
            exception=exception,
            context={},
        )

    @task
    def fetch_balance_and_health(self):
        requests = (
            (
                f"/api/accounts/{self.account_id}/balance",
                "GetBalanceWithSecretHeader",
            ),
            ("/health", "HealthCheckWithSecretHeader"),
        )
        for path, name in requests:
            # The JMX has no CookieManager: do not carry cookies between samplers.
            self.client.cookies.clear()
            with self.client.get(
                path,
                name=name,
                allow_redirects=True,
                timeout=(10, 30),
                catch_response=True,
            ) as response:
                if response.status_code != 200:
                    response.failure(f"Expected 200, got {response.status_code}")


@events.test_start.add_listener
def reset_secret_demo_logging(environment, **_kwargs):
    KeyVaultSecretsCustomer._demo_secret_logged = False
