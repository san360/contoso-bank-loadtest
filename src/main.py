"""
Contoso Bank API — Azure Load Testing demo application.

A deliberately imperfect retail-banking API. Every performance defect in here is
real (not a sleep pretending to be slow) and can be switched on and off at runtime
via POST /admin/config, so the "before / after fix" demo is repeatable and never
fails live.

Planted bottlenecks (all runtime-togglable):
  1. No caching on balance lookups        -> every request hits the "database"
  2. Small connection pool                -> queueing, then a response-time cliff
  3. Global lock on transfers             -> throughput ceiling, no horizontal gain
  4. N+1 query pattern on transactions    -> dependency calls scale with page size
  5. Synchronous third-party fraud check  -> latency floor on the write path
  6. CPU-bound statement generation       -> saturates the worker under modest load
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from transaction_enrichment import FEATURE, enrich_transactions

# --------------------------------------------------------------------------------------
# Optional Azure Monitor / Application Insights wiring.
# Enabled automatically when APPLICATIONINSIGHTS_CONNECTION_STRING is present.
# --------------------------------------------------------------------------------------
_TELEMETRY_ENABLED = False
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor()
        _TELEMETRY_ENABLED = True
    except Exception:  # pragma: no cover - telemetry must never break the app
        _TELEMETRY_ENABLED = False


# --------------------------------------------------------------------------------------
# Runtime configuration — the demo control surface
# --------------------------------------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class DemoConfig:
    """Every knob that makes the demo tell a story."""

    # Bottleneck 1 — caching
    enable_cache: bool = field(default_factory=lambda: _env_bool("ENABLE_CACHE", False))
    cache_ttl_seconds: int = field(default_factory=lambda: _env_int("CACHE_TTL_SECONDS", 30))

    # Bottleneck 2 — connection pool
    db_pool_size: int = field(default_factory=lambda: _env_int("DB_POOL_SIZE", 2))
    db_query_ms: int = field(default_factory=lambda: _env_int("DB_QUERY_MS", 25))

    # Bottleneck 3 — global lock on the write path
    enable_transfer_lock: bool = field(
        default_factory=lambda: _env_bool("ENABLE_TRANSFER_LOCK", True)
    )

    # Bottleneck 4 — N+1 queries
    enable_n_plus_one: bool = field(default_factory=lambda: _env_bool("ENABLE_N_PLUS_ONE", True))

    # Bottleneck 5 — third-party fraud check
    fraud_check_ms: int = field(default_factory=lambda: _env_int("FRAUD_CHECK_MS", 45))
    fraud_check_parallel: bool = field(
        default_factory=lambda: _env_bool("FRAUD_CHECK_PARALLEL", False)
    )

    # Bottleneck 6 — CPU-bound statement generation
    statement_cpu_rounds: int = field(
        default_factory=lambda: _env_int("STATEMENT_CPU_ROUNDS", 18000)
    )

    # Failure injection
    error_rate_percent: float = field(
        default_factory=lambda: float(os.getenv("ERROR_RATE_PERCENT", "0"))
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enable_cache": self.enable_cache,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "db_pool_size": self.db_pool_size,
            "db_query_ms": self.db_query_ms,
            "enable_transfer_lock": self.enable_transfer_lock,
            "enable_n_plus_one": self.enable_n_plus_one,
            "fraud_check_ms": self.fraud_check_ms,
            "fraud_check_parallel": self.fraud_check_parallel,
            "statement_cpu_rounds": self.statement_cpu_rounds,
            "error_rate_percent": self.error_rate_percent,
        }


CONFIG = DemoConfig()

# Named presets so you can flip the whole demo state in one call.
PRESETS: dict[str, dict[str, Any]] = {
    "bottlenecked": {
        "enable_cache": False,
        "db_pool_size": 2,
        "db_query_ms": 25,
        "enable_transfer_lock": True,
        "enable_n_plus_one": True,
        "fraud_check_parallel": False,
        "statement_cpu_rounds": 30000,
    },
    "optimized": {
        "enable_cache": True,
        "db_pool_size": 64,
        "db_query_ms": 25,
        "enable_transfer_lock": False,
        "enable_n_plus_one": False,
        "fraud_check_parallel": True,
        "statement_cpu_rounds": 1500,
    },
}

MODE_EXPLANATIONS = {
    "bottlenecked": (
        "The demo starting state: this same application code ships with six realistic "
        "performance defects switched on so Azure Load Testing can surface them under load."
    ),
    "optimized": (
        "The after-the-fix comparison: the same code with those defects switched off. "
        "Switch with POST /admin/config and no redeploy or restart."
    ),
}

SETTING_METADATA: dict[str, dict[str, str]] = {
    "enable_cache": {
        "meaning": "Balance lookup caching",
        "impact": (
            "GET /api/accounts/{id}/balance — when disabled, every balance request repeats "
            "account, authorisation and ledger database calls; under load the pool queues and "
            "balance enquiries slow sharply."
        ),
    },
    "cache_ttl_seconds": {
        "meaning": "How long cached balances stay fresh",
        "impact": (
            "GET /api/accounts/{id}/balance — only matters when caching is enabled; a healthy "
            "TTL lets repeated balance checks return from memory instead of the database."
        ),
    },
    "db_pool_size": {
        "meaning": "Database connection pool size",
        "impact": (
            "All database-backed journeys, especially balance, transaction history and transfers — "
            "a tiny pool creates queueing and a response-time cliff as concurrency rises."
        ),
    },
    "db_query_ms": {
        "meaning": "Simulated latency for each database query",
        "impact": (
            "All database-backed journeys — this is the per-query cost that makes extra round-trips "
            "visible; N+1 and uncached paths multiply it."
        ),
    },
    "enable_transfer_lock": {
        "meaning": "Process-wide transfer booking lock",
        "impact": (
            "POST /api/transfers — when enabled, all transfers are serialized behind one lock, "
            "creating a throughput ceiling that does not improve with more concurrent users."
        ),
    },
    "enable_n_plus_one": {
        "meaning": "N+1 transaction-detail query pattern",
        "impact": (
            "GET /api/accounts/{id}/transactions — when enabled, one list query is followed by "
            "one detail query per row, so dependency calls and latency grow with page size."
        ),
    },
    "fraud_check_ms": {
        "meaning": "Latency of each fraud and sanctions provider call",
        "impact": (
            "POST /api/transfers — each write waits for both third-party checks; this sets the "
            "external-service latency floor on payment initiation."
        ),
    },
    "fraud_check_parallel": {
        "meaning": "Whether fraud and sanctions checks run concurrently",
        "impact": (
            "POST /api/transfers — when disabled, fraud and sanctions checks run one after the "
            "other, doubling third-party wait time on the write path."
        ),
    },
    "statement_cpu_rounds": {
        "meaning": "CPU work used to generate a statement",
        "impact": (
            "GET /api/statements/{id} — high rounds burn CPU on the request path, saturating the "
            "worker and pushing p95 latency up even at modest load."
        ),
    },
    "error_rate_percent": {
        "meaning": "Injected downstream failure rate",
        "impact": (
            "All banking API journeys — when above zero, requests can fail with 503 so demos can "
            "exercise load-test failure criteria and error-rate charts."
        ),
    },
}


# --------------------------------------------------------------------------------------
# Simulated infrastructure
# --------------------------------------------------------------------------------------


class SimulatedDatabase:
    """
    Stands in for a real relational database.

    The connection pool is a genuine asyncio.Semaphore, so pool exhaustion produces a
    real queueing cliff under concurrency — not a scripted delay. This is the single
    most convincing bottleneck in the demo because the response-time curve has the
    characteristic knee that engineers recognise immediately.
    """

    def __init__(self) -> None:
        self._pool = asyncio.Semaphore(CONFIG.db_pool_size)
        self._pool_size = CONFIG.db_pool_size
        self.queries_executed = 0
        self.peak_wait_ms = 0.0

    def resize(self, size: int) -> None:
        self._pool = asyncio.Semaphore(size)
        self._pool_size = size

    @property
    def pool_size(self) -> int:
        return self._pool_size

    @asynccontextmanager
    async def connection(self):
        started = time.perf_counter()
        pool = self._pool
        async with pool:
            waited_ms = (time.perf_counter() - started) * 1000
            self.peak_wait_ms = max(self.peak_wait_ms, waited_ms)
            yield

    async def query(self, _statement: str) -> None:
        async with self.connection():
            self.queries_executed += 1
            # Simulated round-trip + server-side execution time.
            await asyncio.sleep(CONFIG.db_query_ms / 1000)


class TtlCache:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            self._entries.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key: str, value: Any) -> None:
        self._entries[key] = (time.monotonic() + CONFIG.cache_ttl_seconds, value)

    def clear(self) -> None:
        self._entries.clear()
        self.hits = 0
        self.misses = 0


DB = SimulatedDatabase()
CACHE = TtlCache()

# Bottleneck 3: a single global lock serialising every transfer in the process.
# Modelled on the real-world anti-pattern of a process-wide lock guarding a ledger
# write. Throughput flatlines and adding instances does not help proportionally.
TRANSFER_LOCK = asyncio.Lock()


# --------------------------------------------------------------------------------------
# Seed data
# --------------------------------------------------------------------------------------

ACCOUNT_COUNT = _env_int("ACCOUNT_COUNT", 2000)
CURRENCIES = ["GBP", "EUR", "USD"]
MERCHANTS = [
    "TESCO STORES", "TFL TRAVEL", "AMAZON UK", "SHELL FUEL", "SPOTIFY",
    "BRITISH GAS", "COUNCIL TAX", "SALARY CREDIT", "ATM WITHDRAWAL",
    "TRANSFER OUT", "MORTGAGE PAYMENT", "PENSION CONTRIB",
]


@dataclass
class Account:
    account_id: str
    customer_name: str
    sort_code: str
    balance_minor: int
    currency: str
    opened: str


@dataclass
class Transaction:
    transaction_id: str
    account_id: str
    amount_minor: int
    currency: str
    description: str
    booked_at: str
    status: str


ACCOUNTS: dict[str, Account] = {}
TRANSACTIONS: dict[str, list[Transaction]] = {}
TRANSFERS: dict[str, dict[str, Any]] = {}


def seed_data() -> None:
    """Deterministic seed so every demo run looks identical."""
    rng = random.Random(20260807)
    ACCOUNTS.clear()
    TRANSACTIONS.clear()
    TRANSFERS.clear()

    for i in range(ACCOUNT_COUNT):
        account_id = f"ACC-{1000 + i}"
        account = Account(
            account_id=account_id,
            customer_name=f"Customer {1000 + i}",
            sort_code=f"{rng.randint(10, 99)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}",
            balance_minor=rng.randint(50_000, 9_500_000),
            currency=CURRENCIES[i % len(CURRENCIES)],
            opened=(datetime.now(timezone.utc) - timedelta(days=rng.randint(200, 4000)))
            .date()
            .isoformat(),
        )
        ACCOUNTS[account_id] = account

        history = []
        for j in range(40):
            history.append(
                Transaction(
                    transaction_id=f"TXN-{i}-{j}",
                    account_id=account_id,
                    amount_minor=rng.randint(-45_000, 60_000),
                    currency=account.currency,
                    description=rng.choice(MERCHANTS),
                    booked_at=(
                        datetime.now(timezone.utc) - timedelta(days=j, hours=rng.randint(0, 23))
                    ).isoformat(),
                    status="BOOKED",
                )
            )
        TRANSACTIONS[account_id] = history


seed_data()


# --------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------


class TransferRequest(BaseModel):
    from_account: str = Field(..., examples=["ACC-1001"])
    to_account: str = Field(..., examples=["ACC-1002"])
    amount_minor: int = Field(..., gt=0, description="Amount in minor units (pence/cents)")
    currency: str = Field(default="GBP")
    reference: str = Field(default="", max_length=64)


class ConfigUpdate(BaseModel):
    # Reject unknown fields instead of ignoring them. Every field here is
    # snake_case; the App Service application settings that mirror them are
    # UPPER_SNAKE. The easy mistake is to post camelCase ("enableCache"), and
    # Pydantic's default behaviour is to *silently ignore* it and return 200 —
    # so the demo appears to apply a fix that never took effect. Failing loudly
    # is the same principle as setting fail criteria on a load test: a silent
    # success is worse than an obvious failure.
    model_config = ConfigDict(extra="forbid")

    preset: str | None = None
    enable_cache: bool | None = None
    cache_ttl_seconds: int | None = None
    db_pool_size: int | None = None
    db_query_ms: int | None = None
    enable_transfer_lock: bool | None = None
    enable_n_plus_one: bool | None = None
    fraud_check_ms: int | None = None
    fraud_check_parallel: bool | None = None
    statement_cpu_rounds: int | None = None
    error_rate_percent: float | None = None


# --------------------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------------------

app = FastAPI(
    title="Contoso Bank API",
    version="1.0.0",
    description=(
        "Demo retail-banking API for Azure Load Testing. "
        "Performance characteristics are switchable at runtime via /admin/config."
    ),
)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

BUILD_INFO_FILE = Path(__file__).resolve().parent / "build-info.json"
BUILD_INFO = (
    json.loads(BUILD_INFO_FILE.read_text(encoding="utf-8"))
    if BUILD_INFO_FILE.exists()
    else {"source_sha": "local", "pipeline_id": "local"}
)
if BUILD_INFO["source_sha"] != "local" and not re.fullmatch(
    r"[0-9a-f]{40}", BUILD_INFO["source_sha"]
):
    raise RuntimeError("build-info.json does not contain a full source commit SHA")
DEMO_ADMIN_TOKEN = os.getenv("DEMO_ADMIN_TOKEN", "")

STARTED_AT = time.time()
REQUEST_COUNT = 0


@app.middleware("http")
async def correlation_and_timing(request: Request, call_next):
    global REQUEST_COUNT
    if request.url.path.startswith("/admin") and DEMO_ADMIN_TOKEN:
        supplied = request.headers.get("X-Demo-Admin-Token", "")
        if not hmac.compare_digest(supplied, DEMO_ADMIN_TOKEN):
            return JSONResponse(status_code=401, content={"error": "Admin token required"})
    REQUEST_COUNT += 1
    correlation_id = request.headers.get("X-Correlation-Id", str(uuid.uuid4()))
    started = time.perf_counter()
    response: Response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Correlation-Id"] = correlation_id
    response.headers["X-Server-Time-Ms"] = f"{elapsed_ms:.1f}"
    return response


def _maybe_inject_error() -> None:
    if CONFIG.error_rate_percent > 0 and random.random() * 100 < CONFIG.error_rate_percent:
        raise HTTPException(status_code=503, detail="Downstream core banking unavailable")


_MISSING = object()


def _format_config_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _mode_from_config(cfg: dict[str, Any]) -> str:
    if all(cfg.get(key) == value for key, value in PRESETS["optimized"].items()):
        return "OPTIMIZED"
    if all(cfg.get(key) == value for key, value in PRESETS["bottlenecked"].items()):
        return "BOTTLENECKED"
    return "OPTIMIZED" if cfg["enable_cache"] and not cfg["enable_transfer_lock"] else "BOTTLENECKED"


def _preset_value(setting: str, preset: str) -> Any:
    return PRESETS[preset].get(setting, _MISSING)


def _preset_value_html(setting: str, preset: str) -> str:
    value = _preset_value(setting, preset)
    if value is _MISSING:
        return '<span class="muted">Unchanged by preset</span>'
    return f"<code>{escape(_format_config_value(value))}</code>"


def _current_value_source(setting: str, value: Any) -> str:
    matches = [
        preset for preset, values in PRESETS.items() if setting in values and values[setting] == value
    ]
    is_set_by_any_preset = any(setting in values for values in PRESETS.values())

    if len(matches) == len(PRESETS):
        return "Both presets"
    if matches:
        return matches[0]
    if is_set_by_any_preset:
        return "Custom value"
    return "Runtime-only setting"


def _setting_legend_items() -> list[dict[str, Any]]:
    return [
        {
            "setting": setting,
            "meaning": SETTING_METADATA[setting]["meaning"],
            "impact": SETTING_METADATA[setting]["impact"],
            "preset_values": {
                preset: values[setting]
                for preset, values in PRESETS.items()
                if setting in values
            },
            "set_by_preset": {
                preset: setting in values for preset, values in PRESETS.items()
            },
        }
        for setting in CONFIG.as_dict().keys()
    ]


async def _fraud_check(transfer: TransferRequest) -> dict[str, Any]:
    """Simulated call to a third-party fraud/AML provider."""
    await asyncio.sleep(CONFIG.fraud_check_ms / 1000)
    risk_seed = hashlib.sha256(
        f"{transfer.from_account}{transfer.to_account}{transfer.amount_minor}".encode()
    ).hexdigest()
    score = int(risk_seed[:4], 16) % 100
    return {"provider": "contoso-fraud-svc", "score": score, "decision": "ALLOW" if score < 97 else "REVIEW"}


async def _sanctions_check(transfer: TransferRequest) -> dict[str, Any]:
    """Second third-party call. Run serially or in parallel depending on config."""
    await asyncio.sleep(CONFIG.fraud_check_ms / 1000)
    return {"provider": "contoso-sanctions-svc", "hit": False}


# --------------------------------------------------------------------------------------
# Operational endpoints
# --------------------------------------------------------------------------------------


@app.get("/health", tags=["ops"])
async def health() -> dict[str, Any]:
    """Cheap liveness probe. Deliberately touches nothing slow."""
    return {
        "status": "healthy",
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
        "telemetry": _TELEMETRY_ENABLED,
        "version": app.version,
    }


@app.get("/ready", tags=["ops"])
async def ready() -> dict[str, Any]:
    await DB.query("SELECT 1")
    return {"status": "ready", "accounts": len(ACCOUNTS)}


@app.get("/version", tags=["ops"])
async def version() -> dict[str, str]:
    return {
        "source_sha": BUILD_INFO["source_sha"],
        "pipeline_id": str(BUILD_INFO["pipeline_id"]),
        "feature": FEATURE,
    }


@app.get("/", response_class=FileResponse, tags=["ops"])
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/status", response_class=HTMLResponse, tags=["ops"])
async def status() -> str:
    cfg = CONFIG.as_dict()
    rows = "".join(
        "<tr>"
        f"<td><code>{escape(k)}</code></td>"
        f"<td><code>{escape(_format_config_value(v))}</code></td>"
        f"<td><span class=\"match {escape(_current_value_source(k, v).lower().replace(' ', '-'))}\">"
        f"{escape(_current_value_source(k, v))}</span></td>"
        "</tr>"
        for k, v in cfg.items()
    )
    legend_rows = "".join(
        "<tr>"
        f"<td><code>{escape(item['setting'])}</code></td>"
        f"<td>{escape(item['meaning'])}</td>"
        f"<td>{_preset_value_html(item['setting'], 'bottlenecked')}</td>"
        f"<td>{_preset_value_html(item['setting'], 'optimized')}</td>"
        f"<td>{escape(item['impact'])}</td>"
        "</tr>"
        for item in _setting_legend_items()
    )
    mode = _mode_from_config(cfg)
    colour = "#107c10" if mode == "OPTIMIZED" else "#a4262c"
    mode_key = mode.lower()
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Contoso Bank API</title>
<style>
 body{{font-family:Segoe UI,system-ui,sans-serif;margin:2rem auto;max-width:1100px;color:#242424}}
 h1{{margin-bottom:.2rem}} .sub{{color:#616161;margin-top:0}}
 .badge{{display:inline-block;padding:.35rem .9rem;border-radius:1rem;color:#fff;background:{colour};font-weight:600}}
 .explainer{{border:1px solid #e0e0e0;border-radius:16px;background:#faf9f8;padding:1rem;margin:1rem 0 1.25rem}}
 .explainer p{{margin:.35rem 0;color:#424242;line-height:1.55}}
 table{{border-collapse:collapse;width:100%;margin-top:1rem}}
 td,th{{border:1px solid #e0e0e0;padding:.5rem .75rem;text-align:left}}
 th{{background:#faf9f8}} code{{background:#f3f2f1;padding:.1rem .35rem;border-radius:3px}}
 .legend-table td:last-child{{min-width:280px}}
 .muted{{color:#616161}}
 .match{{display:inline-block;border-radius:999px;padding:.2rem .55rem;background:#f3f2f1;font-weight:600}}
 .match.bottlenecked{{background:#fde7e9;color:#a4262c}}
 .match.optimized{{background:#dff6dd;color:#107c10}}
 .match.both-presets{{background:#e8f3ff;color:#005a9e}}
 .match.custom-value{{background:#fff4ce;color:#8a4b00}}
 ul{{line-height:1.8}}
</style></head><body>
<h1>Contoso Bank API</h1>
<p class="sub">Demo target for Azure Load Testing &middot; current mode: <span class="badge">{mode}</span></p>
<section class="explainer" aria-labelledby="mode-explainer-heading">
 <h3 id="mode-explainer-heading">What {mode} means</h3>
 <p><strong>Bottlenecked</strong> is the demo starting state: the app ships with six realistic performance defects switched on.</p>
 <p><strong>Optimized</strong> is the same code with those defects switched off for the after-the-fix comparison.</p>
 <p>Switch between them at runtime with <code>POST /admin/config</code> and <code>{{"preset":"bottlenecked"}}</code> or <code>{{"preset":"optimized"}}</code>; no redeploy or restart is required. Current mode detail: {escape(MODE_EXPLANATIONS[mode_key])}</p>
</section>
<h3>Endpoints</h3>
<ul>
 <li><code>GET /api/accounts/{{id}}/balance</code> — balance enquiry (cacheable)</li>
 <li><code>GET /api/accounts/{{id}}/transactions</code> — transaction history (N+1 pattern)</li>
 <li><code>POST /api/transfers</code> — payment initiation (lock + fraud checks)</li>
 <li><code>GET /api/statements/{{id}}</code> — statement generation (CPU bound)</li>
 <li><code>GET /docs</code> — OpenAPI / Swagger UI</li>
 <li><code>GET /admin/config</code> · <code>POST /admin/config</code> · <code>POST /admin/reset</code></li>
</ul>
<h3>Mode legend</h3>
<table class="legend-table"><tr><th>Setting</th><th>Plain-English meaning</th><th>Value in bottlenecked</th><th>Value in optimized</th><th>Endpoint / journey degraded and symptom under load</th></tr>{legend_rows}</table>
<h3>Runtime configuration</h3>
<table><tr><th>Setting</th><th>Live value</th><th>Live value matches</th></tr>{rows}</table>
</body></html>"""


@app.get("/admin/config", tags=["admin"])
async def get_config() -> dict[str, Any]:
    return {
        "config": CONFIG.as_dict(),
        "presets": list(PRESETS.keys()),
        "runtime": {
            "requests_served": REQUEST_COUNT,
            "db_queries": DB.queries_executed,
            "db_pool_size": DB.pool_size,
            "db_peak_pool_wait_ms": round(DB.peak_wait_ms, 1),
            "cache_hits": CACHE.hits,
            "cache_misses": CACHE.misses,
        },
    }


@app.get("/admin/presets", tags=["admin"])
async def get_presets() -> dict[str, Any]:
    return {
        "modes": MODE_EXPLANATIONS,
        "switching": {
            "endpoint": "POST /admin/config",
            "body_examples": [{"preset": name} for name in PRESETS.keys()],
            "requires_restart": False,
            "requires_redeploy": False,
        },
        "presets": PRESETS,
        "settings": _setting_legend_items(),
    }


@app.post("/admin/config", tags=["admin"])
async def set_config(update: ConfigUpdate) -> dict[str, Any]:
    """
    Flip bottlenecks on and off live. This is what makes the
    'run test -> find bottleneck -> fix -> re-run -> compare' demo repeatable.
    """
    changes = update.model_dump(exclude_none=True)

    preset = changes.pop("preset", None)
    if preset:
        if preset not in PRESETS:
            raise HTTPException(status_code=400, detail=f"Unknown preset '{preset}'")
        changes = {**PRESETS[preset], **changes}

    for key, value in changes.items():
        if not hasattr(CONFIG, key):
            raise HTTPException(status_code=400, detail=f"Unknown setting '{key}'")
        setattr(CONFIG, key, value)

    if "db_pool_size" in changes:
        DB.resize(int(changes["db_pool_size"]))
    if changes.get("enable_cache") is False:
        CACHE.clear()

    return {"applied": changes, "config": CONFIG.as_dict()}


@app.post("/admin/reset", tags=["admin"])
async def reset() -> dict[str, Any]:
    """Reset seed data and counters between test runs."""
    global REQUEST_COUNT
    seed_data()
    CACHE.clear()
    DB.queries_executed = 0
    DB.peak_wait_ms = 0.0
    REQUEST_COUNT = 0
    return {"status": "reset", "accounts": len(ACCOUNTS)}


# --------------------------------------------------------------------------------------
# Banking endpoints
# --------------------------------------------------------------------------------------


@app.get("/api/accounts", tags=["accounts"])
async def list_accounts(limit: int = 25, offset: int = 0) -> dict[str, Any]:
    _maybe_inject_error()
    await DB.query("SELECT TOP (@limit) * FROM accounts")
    ids = list(ACCOUNTS.keys())[offset : offset + min(limit, 100)]
    return {
        "total": len(ACCOUNTS),
        "items": [ACCOUNTS[i].__dict__ for i in ids],
    }


@app.get("/api/accounts/{account_id}/balance", tags=["accounts"])
async def get_balance(account_id: str) -> dict[str, Any]:
    """
    Balance enquiry — the highest-volume endpoint in any retail bank.

    BOTTLENECK 1: with `enable_cache=false` every single request performs the full set
    of database round-trips through the connection pool — the account row, pending
    card authorisations and the ledger sum. Under load the pool saturates and this
    endpoint develops a textbook queueing cliff. With caching on, the same work is
    done once per TTL and every other request is served from memory.
    """
    _maybe_inject_error()
    account = ACCOUNTS.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")

    cache_key = f"balance:{account_id}"
    if CONFIG.enable_cache:
        cached = CACHE.get(cache_key)
        if cached is not None:
            return {**cached, "source": "cache"}

    # A real balance enquiry is not one query. It is the account record, the
    # pending authorisations that have not yet settled, and the ledger sum.
    await DB.query("SELECT balance FROM accounts WHERE account_id = @id")
    await DB.query("SELECT SUM(amount) FROM pending_authorisations WHERE account_id = @id")
    await DB.query("SELECT SUM(amount) FROM ledger_entries WHERE account_id = @id")

    payload = {
        "account_id": account.account_id,
        "customer_name": account.customer_name,
        "sort_code": account.sort_code,
        "available_balance": round(account.balance_minor / 100, 2),
        "currency": account.currency,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    if CONFIG.enable_cache:
        CACHE.set(cache_key, payload)
    return {**payload, "source": "database"}


@app.get("/api/accounts/{account_id}/transactions", tags=["accounts"])
async def get_transactions(account_id: str, page_size: int = 20) -> dict[str, Any]:
    """
    Transaction history.

    BOTTLENECK 4: with `enable_n_plus_one=true` the handler issues one query for the
    list plus one query per row (classic ORM lazy-loading defect). Dependency call
    volume scales with page size, which is invisible at one user and catastrophic at
    a thousand.
    """
    _maybe_inject_error()
    if account_id not in ACCOUNTS:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")

    page_size = max(1, min(page_size, 50))
    await DB.query("SELECT TOP (@n) * FROM transactions WHERE account_id = @id")
    rows = TRANSACTIONS[account_id][:page_size]

    if CONFIG.enable_n_plus_one:
        for _row in rows:
            await DB.query("SELECT * FROM transaction_details WHERE transaction_id = @id")

    return {
        "account_id": account_id,
        "count": len(rows),
        "query_pattern": "n+1" if CONFIG.enable_n_plus_one else "batched",
        "items": await enrich_transactions([r.__dict__ for r in rows], DB.query),
    }


@app.post("/api/transfers", status_code=201, tags=["payments"])
async def create_transfer(transfer: TransferRequest) -> dict[str, Any]:
    """
    Payment initiation — the endpoint that matters, and the one that breaks.

    BOTTLENECK 3: a process-wide lock serialises every transfer.
    BOTTLENECK 5: two third-party checks run sequentially rather than concurrently.
    """
    _maybe_inject_error()

    source = ACCOUNTS.get(transfer.from_account)
    target = ACCOUNTS.get(transfer.to_account)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Account {transfer.from_account} not found")
    if target is None:
        raise HTTPException(status_code=404, detail=f"Account {transfer.to_account} not found")
    if source.account_id == target.account_id:
        raise HTTPException(status_code=400, detail="Source and target accounts must differ")

    if CONFIG.fraud_check_parallel:
        fraud, sanctions = await asyncio.gather(
            _fraud_check(transfer), _sanctions_check(transfer)
        )
    else:
        fraud = await _fraud_check(transfer)
        sanctions = await _sanctions_check(transfer)

    async def _book() -> dict[str, Any]:
        await DB.query("SELECT balance FROM accounts WITH (UPDLOCK) WHERE account_id = @id")
        if source.balance_minor < transfer.amount_minor:
            raise HTTPException(status_code=422, detail="Insufficient funds")

        source.balance_minor -= transfer.amount_minor
        target.balance_minor += transfer.amount_minor

        await DB.query("UPDATE accounts SET balance = @b WHERE account_id = @id")
        await DB.query("INSERT INTO ledger (...) VALUES (...)")

        transaction_id = f"TRF-{uuid.uuid4().hex[:12].upper()}"
        record = {
            "transaction_id": transaction_id,
            "from_account": transfer.from_account,
            "to_account": transfer.to_account,
            "amount": round(transfer.amount_minor / 100, 2),
            "currency": transfer.currency,
            "reference": transfer.reference,
            "status": "SETTLED" if fraud["decision"] == "ALLOW" else "PENDING_REVIEW",
            "fraud_check": fraud,
            "sanctions_check": sanctions,
            "booked_at": datetime.now(timezone.utc).isoformat(),
        }
        TRANSFERS[transaction_id] = record
        return record

    if CONFIG.enable_transfer_lock:
        async with TRANSFER_LOCK:
            return await _book()
    return await _book()


@app.get("/api/transfers/{transaction_id}", tags=["payments"])
async def get_transfer(transaction_id: str) -> dict[str, Any]:
    record = TRANSFERS.get(transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Transfer {transaction_id} not found")
    return record


@app.get("/api/statements/{account_id}", tags=["reporting"])
async def get_statement(account_id: str, months: int = 3) -> dict[str, Any]:
    """
    Statement generation — the heavy report endpoint.

    BOTTLENECK 6: genuinely CPU-bound work on the request thread. Even at low request
    rates this saturates worker CPU, which is exactly what makes it show up so clearly
    in the server-side metrics panel of the load test dashboard.
    """
    _maybe_inject_error()
    account = ACCOUNTS.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id} not found")

    await DB.query("SELECT * FROM transactions WHERE account_id = @id AND booked_at > @from")

    # Real CPU burn — repeated hashing. Not a sleep.
    digest = account_id.encode()
    for _ in range(max(0, CONFIG.statement_cpu_rounds)):
        digest = hashlib.sha256(digest).digest()

    rows = TRANSACTIONS[account_id][: months * 12]
    credits = sum(t.amount_minor for t in rows if t.amount_minor > 0)
    debits = sum(-t.amount_minor for t in rows if t.amount_minor < 0)

    return {
        "account_id": account_id,
        "customer_name": account.customer_name,
        "period_months": months,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document_hash": digest.hex()[:32],
        "summary": {
            "opening_balance": round((account.balance_minor + debits - credits) / 100, 2),
            "closing_balance": round(account.balance_minor / 100, 2),
            "total_credits": round(credits / 100, 2),
            "total_debits": round(debits / 100, 2),
            "transaction_count": len(rows),
        },
    }


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status": exc.status_code,
            "path": str(request.url.path),
            "correlation_id": request.headers.get("X-Correlation-Id", ""),
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=_env_int("PORT", 8000),
        reload=_env_bool("RELOAD", False),
    )
