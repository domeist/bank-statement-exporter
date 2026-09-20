"""The canonical transaction shape produced by the editors.

A transaction is a plain dict with a real ``date`` and a **positive** amount;
``kind`` says what the amount means. ``export`` decides how to present it.
"""

from datetime import date as date_type

EXPENSE = "expense"
INCOME = "income"
BILL = "bill"

MONZO = "Monzo"
REVOLUT = "Revolut"

# Expenses first, then bills, then income credits — within the same date,
# Monzo before Revolut.
_KIND_ORDER = {EXPENSE: 0, BILL: 1, INCOME: 2}
_SOURCE_ORDER = {MONZO: 0, REVOLUT: 1}


def transaction(
    *,
    date: date_type,
    amount: float,
    description: str,
    kind: str,
    source: str,
    category: str = "",
    trip: str = "",
    note: str = "",
    rate_failed: bool = False,
) -> dict:
    return {
        "date": date,
        "amount": amount,
        "description": description,
        "kind": kind,
        "source": source,
        "category": category,
        "trip": trip,
        # Original currency/fee detail, and whether the conversion to GBP failed.
        "note": note,
        "rate_failed": rate_failed,
    }


def has_usable_date(row: dict) -> bool:
    """True when the row can be ordered and exported — editors allow blanking a date."""
    return isinstance(row.get("date"), date_type)


def sort_transactions(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (r["date"], _KIND_ORDER.get(r["kind"], 99), _SOURCE_ORDER.get(r["source"], 99)),
    )

