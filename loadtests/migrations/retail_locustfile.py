"""Migration of contoso-bank.jmx, including its shared percentage controllers."""

from __future__ import annotations

import csv
import itertools
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from gevent.lock import Semaphore
from locust import events, task
from locust.env import Environment
from locust.exception import StopUser

from jmeter_runtime import ScheduledHttpUser, ScheduledRun, integer_setting, startup_failure

ACCOUNT_FIELDS = ("accountId", "customerName", "sortCode", "segment")
PERCENTAGES = (60, 20, 15, 5)
TIMEOUT = (10, 60)


class SharedAccounts:
    def __init__(self, path: Path) -> None:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream, delimiter=",", quoting=csv.QUOTE_NONE)
            next(reader, None)
            self.rows = tuple(tuple(row) for row in reader)
        if not self.rows:
            raise ValueError("Accounts CSV has no data after its unconditional first-row skip.")
        if any(len(row) != len(ACCOUNT_FIELDS) for row in self.rows):
            raise ValueError("Accounts CSV must contain exactly four fields on every data row.")
        self.position = 0

    def next_row(self) -> tuple[str, ...]:
        row = self.rows[self.position]
        self.position = (self.position + 1) % len(self.rows)
        return row


class PercentageControllers:
    def __init__(self) -> None:
        self.visits = 0
        self.executions = [0] * len(PERCENTAGES)

    def next_iteration(self) -> tuple[bool, ...]:
        self.visits += 1
        # JMeter 5.6.3 uses a shared rounding threshold, not random task weights.
        selected = tuple(
            100 * count + 50 < percentage * self.visits
            for count, percentage in zip(self.executions, PERCENTAGES)
        )
        for index, execute in enumerate(selected):
            if execute:
                self.executions[index] += 1
        return selected


@dataclass
class RetailRuntime:
    run: ScheduledRun
    accounts: SharedAccounts
    page_size: int
    think_time_ms: int
    controllers: PercentageControllers = field(default_factory=PercentageControllers)
    thread_numbers: Iterator[int] = field(default_factory=lambda: itertools.count(1))
    transfer_numbers: Iterator[int] = field(default_factory=lambda: itertools.count(1))
    lock: Semaphore = field(default_factory=Semaphore)

    def begin_iteration(self) -> tuple[tuple[str, ...], tuple[bool, ...]]:
        with self.lock:
            return self.accounts.next_row(), self.controllers.next_iteration()

    def next_thread_number(self) -> int:
        with self.lock:
            return next(self.thread_numbers)

    def next_transfer_number(self) -> int:
        with self.lock:
            return next(self.transfer_numbers)


runtime: RetailRuntime | None = None


def accounts_path() -> Path:
    configured = os.environ.get("ACCOUNTS_CSV")
    if configured is not None:
        return Path(configured)
    uploaded = Path(__file__).with_name("accounts.csv")
    if uploaded.is_file():
        return uploaded
    return Path(__file__).resolve().parent.parent / "data" / "accounts.csv"


@events.test_start.add_listener
def initialize(environment: Environment, **_kwargs: object) -> None:
    global runtime
    runtime = None
    try:
        runtime = RetailRuntime(
            ScheduledRun.start(environment),
            SharedAccounts(accounts_path()),
            integer_setting("PAGE_SIZE", 20, minimum=1),
            integer_setting("THINK_TIME_MS", 500),
        )
    except (OSError, ValueError) as exc:
        startup_failure(environment, str(exc))


class RetailBankingCustomer(ScheduledHttpUser):
    def on_start(self) -> None:
        if runtime is None:
            logging.error("Retail migration has no initialized runtime; no HTTP will be sent.")
            raise StopUser()
        self.state = runtime
        self.schedule = self.state.run
        self.thread_number = self.state.next_thread_number()
        self.configure_session()
        self.client.headers.update(
            {"Content-Type": "application/json", "Accept": "application/json"}
        )

    def before_request(self) -> None:
        self.schedule.before_sample(self.state.think_time_ms + random.randrange(500))

    def get_asserted(
        self,
        path: str,
        name: str,
        failure_message: str,
        params: dict[str, int] | None = None,
    ) -> None:
        self.before_request()
        with self.client.get(
            path,
            name=name,
            params=params,
            timeout=TIMEOUT,
            allow_redirects=True,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(failure_message)

    def transfer(self, account_id: str) -> None:
        self.before_request()
        amount = random.randint(500, 25000)
        reference = f"LOADTEST-{self.thread_number}-{self.state.next_transfer_number()}"
        body = (
            f'{{"from_account":"{account_id}","to_account":"ACC-2002",'
            f'"amount_minor":{amount},"currency":"GBP","reference":"{reference}"}}'
        )
        transfer_id: str | None = None
        with self.client.post(
            "/api/transfers",
            data=body,
            name="CreateTransfer",
            timeout=TIMEOUT,
            allow_redirects=True,
            catch_response=True,
        ) as response:
            failures = []
            if response.status_code != 201:
                failures.append("Transfer did not return 201")
            try:
                payload = response.json()
            except ValueError:
                failures.append("Transfer response is not valid JSON; follow-up skipped.")
            else:
                value = payload.get("transaction_id") if isinstance(payload, dict) else None
                if (
                    isinstance(value, (str, int))
                    and not isinstance(value, bool)
                    and str(value).strip()
                ):
                    transfer_id = str(value)
                else:
                    failures.append(
                        "Transfer response has no usable transaction_id; follow-up skipped."
                    )
            if failures:
                response.failure("; ".join(failures))
        if transfer_id is not None:
            self.before_request()
            self.client.get(
                f"/api/transfers/{transfer_id}",
                name="GetTransferStatus",
                timeout=TIMEOUT,
                allow_redirects=True,
            )

    @task
    def banking_iteration(self) -> None:
        self.reset_connections()
        self.client.cookies.clear()
        row, selected = self.state.begin_iteration()
        account_id = row[0]
        if selected[0]:
            self.get_asserted(
                f"/api/accounts/{account_id}/balance",
                "GetBalance",
                "Balance enquiry did not return 200",
            )
        if selected[1]:
            self.get_asserted(
                f"/api/accounts/{account_id}/transactions",
                "GetTransactions",
                "Transaction history did not return 200",
                {"page_size": self.state.page_size},
            )
        if selected[2]:
            self.transfer(account_id)
        if selected[3]:
            self.get_asserted(
                f"/api/statements/{account_id}",
                "GenerateStatement",
                "Statement generation did not return 200",
            )
