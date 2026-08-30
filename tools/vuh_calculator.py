#!/usr/bin/env python3
"""Azure Load Testing cost calculator.

Works out virtual user hours (VUH) and cost for a test configuration, applying
the minimum-billing rule and the tiered price break.

Standard library only - runs anywhere, no install step.

    python tools/vuh_calculator.py --vus 250 --engines 4 --minutes 30 --runs-per-month 20

    python tools/vuh_calculator.py --preset all

WHY THIS EXISTS
    "How much will this cost?" is the question that stalls adoption. Being able
    to answer it live, with the customer's own numbers, in ten seconds, is worth
    more than any slide.

PRICES ARE NOT HARDCODED AS TRUTH
    Defaults reflect public list pricing at the time of writing. Enterprise
    Agreement and CSP rates differ. Override with --price and --price-high, or
    better, ask the customer for their rate and use it live.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

# --- Pricing defaults -------------------------------------------------------
# Public list price, USD per virtual user hour. VERIFY BEFORE THE SESSION.
DEFAULT_PRICE_LOW = 0.15  # first 10,000 VUH per month
DEFAULT_PRICE_HIGH = 0.06  # above 10,000 VUH per month
TIER_BREAK_VUH = 10_000

# --- Minimum billing rule (effective 1 March 2026) --------------------------
# 1. A minimum of 10 virtual users per engine is billed.
# 2. A minimum duration of 10 minutes is billed.
MIN_VUS_PER_ENGINE = 10
MIN_MINUTES = 10


@dataclass(frozen=True)
class Scenario:
    name: str
    vus: int  # total virtual users across all engines
    engines: int
    minutes: float
    runs_per_month: float
    note: str = ""


# Scenarios drawn from the actual test configs in this repo, so the numbers on
# screen match the YAML the audience just looked at.
PRESETS: dict[str, Scenario] = {
    "pr-gate": Scenario(
        name="PR gate (loadtests/config/pr-gate.yaml)",
        vus=25,  # 1 engine x 25 threads
        engines=1,
        minutes=3,
        runs_per_month=160,  # ~40 PRs/week
        note="Short and frequent. Hits the billing floor - see the warning.",
    ),
    "peak-load": Scenario(
        name="Nightly peak load (loadtests/config/peak-load.yaml)",
        vus=1000,  # 4 engines x 250 threads
        engines=4,
        minutes=15,
        runs_per_month=20,  # working nights
        note="The regression suite. The bulk of routine spend.",
    ),
    "quarterly-peak": Scenario(
        name="Quarterly capacity test",
        vus=20000,
        engines=80,
        minutes=120,
        runs_per_month=4 / 12,  # 4 per year
        note="The scenario that would otherwise need a 100-VM fleet all year.",
    ),
    "demo-session": Scenario(
        name="This demo session, end to end",
        vus=100,
        engines=1,
        minutes=25,  # ~5 runs of ~5 minutes
        runs_per_month=1,
        note="What today costs. Usually a rounding error - say so.",
    ),
}


@dataclass(frozen=True)
class Result:
    scenario: Scenario
    raw_vuh: float
    billed_vuh_per_run: float
    billed_vuh_per_month: float
    cost_per_run: float
    cost_per_month: float
    floor_applied: bool


def billed_vuh_for_run(vus: int, engines: int, minutes: float) -> tuple[float, float, bool]:
    """Return (raw_vuh, billed_vuh, floor_applied) for a single run."""
    hours = minutes / 60.0
    raw_vuh = vus * hours

    billed_vus = max(vus, MIN_VUS_PER_ENGINE * engines)
    billed_hours = max(minutes, MIN_MINUTES) / 60.0
    billed_vuh = billed_vus * billed_hours

    return raw_vuh, billed_vuh, billed_vuh > raw_vuh + 1e-9


def tiered_cost(vuh: float, price_low: float, price_high: float) -> float:
    """Apply the monthly tier break."""
    if vuh <= TIER_BREAK_VUH:
        return vuh * price_low
    return TIER_BREAK_VUH * price_low + (vuh - TIER_BREAK_VUH) * price_high


def evaluate(scenario: Scenario, price_low: float, price_high: float) -> Result:
    raw, billed, floor = billed_vuh_for_run(scenario.vus, scenario.engines, scenario.minutes)
    monthly_vuh = billed * scenario.runs_per_month

    # Cost per run is derived from the monthly total so the tier break is
    # attributed correctly rather than applied twice.
    monthly_cost = tiered_cost(monthly_vuh, price_low, price_high)
    per_run_cost = monthly_cost / scenario.runs_per_month if scenario.runs_per_month else 0.0

    return Result(
        scenario=scenario,
        raw_vuh=raw,
        billed_vuh_per_run=billed,
        billed_vuh_per_month=monthly_vuh,
        cost_per_run=per_run_cost,
        cost_per_month=monthly_cost,
        floor_applied=floor,
    )


def render(result: Result, currency: str) -> str:
    s = result.scenario
    lines = [
        "",
        f"  {s.name}",
        f"  {'-' * max(len(s.name), 40)}",
        f"  Configuration      : {s.vus:,} VUs x {s.engines} engine(s) x {s.minutes:g} min",
        f"  Frequency          : {s.runs_per_month:g} run(s)/month",
        f"  VUH per run (raw)  : {result.raw_vuh:,.2f}",
        f"  VUH per run (bill) : {result.billed_vuh_per_run:,.2f}",
        f"  VUH per month      : {result.billed_vuh_per_month:,.2f}",
        f"  Cost per run       : {currency}{result.cost_per_run:,.2f}",
        f"  Cost per month     : {currency}{result.cost_per_month:,.2f}",
        f"  Cost per year      : {currency}{result.cost_per_month * 12:,.2f}",
    ]
    if result.floor_applied:
        uplift = result.billed_vuh_per_run / result.raw_vuh if result.raw_vuh else float("inf")
        lines.append(
            f"  !! MINIMUM BILLING APPLIED: charged {uplift:.1f}x the raw VUH."
        )
        lines.append(
            "     Consolidate short tests rather than running many tiny ones."
        )
    if s.note:
        lines.append(f"  Note               : {s.note}")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Azure Load Testing VUH and cost calculator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--vus", type=int, help="Total virtual users across all engines.")
    p.add_argument("--engines", type=int, default=1, help="Engine instances (default: 1).")
    p.add_argument("--minutes", type=float, help="Test duration in minutes.")
    p.add_argument("--runs-per-month", type=float, default=1.0, help="Runs per month (default: 1).")
    p.add_argument("--preset", help="A named preset, or 'all'. Choices: " + ", ".join(PRESETS))
    p.add_argument("--price", type=float, default=DEFAULT_PRICE_LOW, help=f"Price per VUH up to {TIER_BREAK_VUH:,} VUH/mo.")
    p.add_argument("--price-high", type=float, default=DEFAULT_PRICE_HIGH, help=f"Price per VUH above {TIER_BREAK_VUH:,} VUH/mo.")
    p.add_argument("--currency", default="$", help="Currency symbol for display only.")
    args = p.parse_args()

    scenarios: list[Scenario] = []

    if args.preset == "all":
        scenarios = list(PRESETS.values())
    elif args.preset:
        if args.preset not in PRESETS:
            p.error(f"Unknown preset '{args.preset}'. Choices: {', '.join(PRESETS)}")
        scenarios = [PRESETS[args.preset]]
    else:
        if args.vus is None or args.minutes is None:
            p.error("Provide --vus and --minutes, or use --preset.")
        scenarios = [
            Scenario(
                name="Custom scenario",
                vus=args.vus,
                engines=args.engines,
                minutes=args.minutes,
                runs_per_month=args.runs_per_month,
            )
        ]

    print("=" * 62)
    print("  Azure Load Testing - cost estimate")
    print(f"  Rates: {args.currency}{args.price}/VUH up to {TIER_BREAK_VUH:,} VUH/mo, "
          f"{args.currency}{args.price_high}/VUH above")
    print("  Verify current rates and any free allotment before quoting.")
    print("=" * 62)

    total = 0.0
    for s in scenarios:
        r = evaluate(s, args.price, args.price_high)
        print(render(r, args.currency))
        total += r.cost_per_month

    if len(scenarios) > 1:
        print("\n" + "=" * 62)
        print(f"  COMBINED  {args.currency}{total:,.2f}/month   "
              f"{args.currency}{total * 12:,.2f}/year")
        print("=" * 62)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
