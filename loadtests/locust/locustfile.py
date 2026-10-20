"""
Contoso Bank load test — Locust flavour.

This is the same four banking journeys as `../jmeter/contoso-bank.jmx`, written
in ~90 lines of readable Python instead of ~450 lines of XML. It exists for two
reasons in the demo:

1.  It is the format the Azure Load Testing VS Code extension and GitHub Copilot
    generate (docs 09 and 10). Keep this file as the known-good fallback: if the
    live Copilot generation misbehaves in front of the customer, open this file
    and carry on.
2.  It is the honest counter-argument to "we already have JMeter". Show both,
    let the engineers pick. Azure Load Testing runs either, unmodified.

Run locally:
    locust -f locustfile.py --host http://127.0.0.1:8000

Run in Azure Load Testing:
    az load test create --load-test-resource <alt> --resource-group <rg> \
      --test-id contoso-bank-peak-locust \
      --load-test-config-file ../config/peak-load-locust.yaml

Note: `locust` does not install on Windows ARM64 without MSVC build tools
(no prebuilt wheels for geventhttpclient/brotli). It installs cleanly on the
Azure Load Testing engines, which are Azure Linux x86_64.
"""

import logging
import os
import random

from locust import HttpUser, between, events, task

# Accounts seeded by the API (ACC-1000 .. ACC-2999). Kept small and explicit so
# the request mix is predictable and the cache behaviour is easy to reason about.
ACCOUNT_IDS = [f"ACC-{1000 + i}" for i in range(50)]

PAGE_SIZE = int(os.getenv("PAGE_SIZE", "20"))


@events.test_start.add_listener
def on_test_start(environment, **_kwargs):
    print(f"Contoso Bank load test starting against {environment.host}")
    if os.getenv("DEMO_LOG_SECRET", "").lower() == "true":
        api_key = os.getenv("API_KEY")
        if api_key is None or not api_key.strip():
            logging.error("DEMO_LOG_SECRET=true requires a non-empty API_KEY secret.")
            environment.process_exit_code = 1
            environment.runner.quit()
            return
        logging.warning("DEMO ONLY: retrieved API_KEY=%s", api_key)


class RetailBankingCustomer(HttpUser):
    """A retail banking customer. Think time keeps the arrival rate realistic."""

    wait_time = between(0.5, 1.0)

    def on_start(self):
        # Each simulated customer sticks to one account for the whole session,
        # which is what real traffic looks like and what makes per-account
        # caching effects visible.
        self.account_id = random.choice(ACCOUNT_IDS)

    @task(60)
    def check_balance(self):
        """
        Highest-volume retail operation.

        Exposes BOTTLENECK 1 (no caching): every request performs three database
        round-trips through a deliberately small connection pool.
        """
        with self.client.get(
            f"/api/accounts/{self.account_id}/balance",
            name="GetBalance",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Expected 200, got {response.status_code}")

    @task(20)
    def view_transactions(self):
        """
        Exposes BOTTLENECK 4 (N+1 queries): dependency call volume scales with
        page size — invisible at one user, catastrophic at a thousand.
        """
        self.client.get(
            f"/api/accounts/{self.account_id}/transactions?page_size={PAGE_SIZE}",
            name="GetTransactions",
        )

    @task(15)
    def make_transfer(self):
        """
        The money-movement write path.

        Exposes BOTTLENECK 3 (a global lock serialising every transfer) and
        BOTTLENECK 5 (fraud, sanctions and limit checks run sequentially).
        """
        payload = {
            "from_account": self.account_id,
            "to_account": random.choice(ACCOUNT_IDS),
            # Amounts are in minor units (pence), which is how a real ledger
            # stores money. Never load test a payments API with floats.
            "amount_minor": random.randint(500, 25000),
            "currency": "GBP",
            "reference": f"LOCUST-{random.randint(100000, 999999)}",
        }
        with self.client.post(
            "/api/transfers", json=payload, name="CreateTransfer", catch_response=True
        ) as response:
            if response.status_code != 201:
                response.failure(f"Expected 201, got {response.status_code}")
                return
            # Correlated follow-up read: proves the script handles dynamic ids,
            # which a simple URL-based test cannot.
            transaction_id = response.json().get("transaction_id")
            if transaction_id:
                self.client.get(
                    f"/api/transfers/{transaction_id}", name="GetTransferStatus"
                )

    @task(5)
    def download_statement(self):
        """
        Low volume, high cost.

        Exposes BOTTLENECK 6 (a genuinely CPU-bound handler) — the one that shows
        up as application CPU saturation rather than as latency alone.
        """
        self.client.get(f"/api/statements/{self.account_id}", name="GenerateStatement")
