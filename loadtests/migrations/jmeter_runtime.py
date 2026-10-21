"""Shared runtime behavior for the two inspected JMeter migrations."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from http.cookiejar import Cookie, DefaultCookiePolicy
from time import monotonic
from urllib.parse import urlsplit

from gevent import spawn, sleep
from locust import HttpUser, constant
from locust.env import Environment
from locust.exception import StopUser
from locust.runners import LocalRunner
from requests import Response


def integer_setting(name: str, default: int, minimum: int = 0) -> int:
    value = os.environ.get(name, str(default))
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return result


@dataclass(frozen=True)
class ScheduledRun:
    started_at: float
    duration: int

    @classmethod
    def start(cls, environment: Environment) -> ScheduledRun:
        if not isinstance(environment.runner, LocalRunner):
            raise ValueError(
                "Use one LocalRunner per engine; upstream master/worker mode "
                "does not preserve the engine-shared CSV and controller scope."
            )
        options = environment.parsed_options
        if options is None or not isinstance(options.run_time, int) or options.run_time <= 0:
            raise ValueError("A positive --run-time is required; use a supplied .conf file.")
        if options.num_users is None or options.num_users <= 0 or options.spawn_rate <= 0:
            raise ValueError("Positive --users and --spawn-rate values are required.")
        host = environment.host
        if not host:
            base_url = os.environ.get("BASE_URL")
            if not base_url:
                raise ValueError("An explicit --host or BASE_URL is required.")
            host = f"https://{base_url}"
        parsed = urlsplit(host)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("The host must be an HTTP(S) origin without credentials or a path.")
        environment.host = host.rstrip("/")
        return cls(monotonic(), options.run_time)

    def before_sample(self, delay_ms: int = 0) -> None:
        remaining = self.started_at + self.duration - monotonic()
        if remaining <= 0:
            raise StopUser()
        if delay_ms:
            sleep(min(delay_ms / 1000, remaining))
        if monotonic() >= self.started_at + self.duration:
            raise StopUser()


def startup_failure(environment: Environment, message: str) -> None:
    logging.error("Migration startup failed: %s", message)
    environment.process_exit_code = 1
    if environment.runner is not None:
        spawn(environment.runner.quit)


class RejectCookies(DefaultCookiePolicy):
    def set_ok(self, cookie: Cookie, request: object) -> bool:
        return False

    def return_ok(self, cookie: Cookie, request: object) -> bool:
        return False


class ScheduledHttpUser(HttpUser):
    abstract = True
    wait_time = constant(0)
    schedule: ScheduledRun

    def configure_session(self) -> None:
        self.client.trust_env = False
        self.client.max_redirects = 20

    def disable_cookies(self) -> None:
        self.client.cookies.clear()
        self.client.cookies.set_policy(RejectCookies())
        self.client.hooks["response"].append(self._reject_redirect_cookies)

    def _reject_redirect_cookies(self, response: Response, **_kwargs: object) -> Response:
        # Requests otherwise creates an accepting cookie jar for redirect hops.
        response.cookies.clear()
        response.request.headers.pop("Cookie", None)
        response.request.prepare_cookies(self.client.cookies)
        return response

    def reset_connections(self) -> None:
        self.client.close()

    def on_stop(self) -> None:
        self.client.close()
