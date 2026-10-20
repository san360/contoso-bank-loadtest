"""Scripted fix: preserve the feature and fetch the merchant directory once."""

from collections.abc import Awaitable, Callable
from typing import Any

from merchant_catalog import merchant_details

FEATURE = "merchant-category-v1"


async def enrich_transactions(
    items: list[dict[str, Any]], query: Callable[[str], Awaitable[None]]
) -> list[dict[str, Any]]:
    if items:
        await query("SELECT name, category FROM merchants WHERE name IN (@names)")
    return [
        {**item, "merchant": merchant_details(item["description"])}
        for item in items
    ]
