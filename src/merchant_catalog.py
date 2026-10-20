"""Deterministic merchant directory for the isolated GitLab feature demo."""

MERCHANT_CATEGORIES = {
    "TESCO STORES": "Groceries",
    "TFL TRAVEL": "Transport",
    "AMAZON UK": "Shopping",
    "SHELL FUEL": "Transport",
    "SPOTIFY": "Entertainment",
    "BRITISH GAS": "Utilities",
    "COUNCIL TAX": "Taxes",
    "SALARY CREDIT": "Income",
    "ATM WITHDRAWAL": "Cash",
    "TRANSFER OUT": "Transfers",
    "MORTGAGE PAYMENT": "Housing",
    "PENSION CONTRIB": "Savings",
}


def merchant_details(description: str) -> dict[str, str]:
    return {
        "name": description,
        "category": MERCHANT_CATEGORIES.get(description, "Uncategorized"),
    }
