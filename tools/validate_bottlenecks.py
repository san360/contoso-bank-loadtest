"""Validate that the planted bottlenecks actually manifest under concurrency."""
import asyncio
import os
import statistics
import sys

import httpx

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")


async def hammer(client, method, path, concurrency, total, body=None):
    sem = asyncio.Semaphore(concurrency)
    latencies, errors = [], 0

    async def one():
        nonlocal errors
        async with sem:
            t0 = asyncio.get_event_loop().time()
            try:
                r = await client.request(method, BASE + path, json=body, timeout=60)
                if r.status_code >= 400:
                    errors += 1
            except Exception:
                errors += 1
            latencies.append((asyncio.get_event_loop().time() - t0) * 1000)

    t0 = asyncio.get_event_loop().time()
    await asyncio.gather(*[one() for _ in range(total)])
    wall = asyncio.get_event_loop().time() - t0
    latencies.sort()
    return {
        "avg": statistics.mean(latencies),
        "p95": latencies[int(len(latencies) * 0.95) - 1],
        "rps": total / wall,
        "errors": errors,
    }


def show(label, r):
    print(f"  {label:<14} avg={r['avg']:7.1f}ms  p95={r['p95']:7.1f}ms  "
          f"rps={r['rps']:7.1f}  errors={r['errors']}")


async def main():
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=500)
    async with httpx.AsyncClient(limits=limits) as client:
        results = {}
        for preset in ("bottlenecked", "optimized"):
            await client.post(f"{BASE}/admin/config", json={"preset": preset})
            await client.post(f"{BASE}/admin/reset")
            print(f"\n=== preset: {preset} ===")

            # Warm-up: mirrors the ramp-up phase of a real load test. Uses the
            # same concurrency as the measured run so the connection pool is
            # fully established and we measure steady state, not TCP setup.
            await hammer(client, "GET", "/api/accounts/ACC-1001/balance", 40, 120)

            # Balance is a light read; at very high concurrency the local
            # single-worker uvicorn ceiling — not the connection pool — becomes
            # the binding constraint and hides the cache effect. Concurrency 40
            # keeps the pool as the bottleneck.
            r = await hammer(client, "GET", "/api/accounts/ACC-1001/balance", 40, 400)
            show("balance", r)
            results[(preset, "balance")] = r

            r = await hammer(client, "GET", "/api/accounts/ACC-1001/transactions?page_size=20", 50, 200)
            show("transactions", r)
            results[(preset, "transactions")] = r

            body = {"from_account": "ACC-1001", "to_account": "ACC-1002",
                    "amount_minor": 100, "currency": "GBP", "reference": "load"}
            r = await hammer(client, "POST", "/api/transfers", 50, 200, body)
            show("transfers", r)
            results[(preset, "transfers")] = r

            r = await hammer(client, "GET", "/api/statements/ACC-1001?months=3", 20, 60)
            show("statements", r)
            results[(preset, "statements")] = r

        print("\n=== improvement (bottlenecked -> optimized) ===")
        ok = True
        for ep in ("balance", "transactions", "transfers", "statements"):
            b = results[("bottlenecked", ep)]["p95"]
            o = results[("optimized", ep)]["p95"]
            factor = b / o if o else float("inf")
            verdict = "OK" if factor >= 1.8 else "WEAK"
            if factor < 1.8:
                ok = False
            print(f"  {ep:<14} p95 {b:7.1f}ms -> {o:7.1f}ms   {factor:5.1f}x   [{verdict}]")
        return 0 if ok else 1


sys.exit(asyncio.run(main()))

