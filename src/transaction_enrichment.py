"""The GitLab demo controller adds merchant enrichment here in a real commit."""

from collections.abc import Awaitable, Callable
from typing import Any

FEATURE = "none"


async def enrich_transactions(
    items: list[dict[str, Any]], query: Callable[[str], Awaitable[None]]
) -> list[dict[str, Any]]:
    return items
