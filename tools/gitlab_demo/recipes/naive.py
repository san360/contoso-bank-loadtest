"""Scripted feature commit: correct output, but one directory query per row."""

from collections.abc import Awaitable, Callable
from typing import Any

from merchant_catalog import merchant_details

FEATURE = "merchant-category-v1"


async def enrich_transactions(
    items: list[dict[str, Any]], query: Callable[[str], Awaitable[None]]
) -> list[dict[str, Any]]:
    enriched = []
    for item in items:
        await query("SELECT name, category FROM merchants WHERE name = @name")
        enriched.append({**item, "merchant": merchant_details(item["description"])})
    return enriched
