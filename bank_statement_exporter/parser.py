"""Parsing of raw Monzo API transactions into tidy DataFrames.

Amounts are always positive; the ``kind`` of a row (expense, income, bill) says
what the amount means. Dates are real ``date`` objects — formatting for a
particular output target happens in that target's module.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from .config import DEFAULT_MONZO_CATEGORY_MAP, DEFAULT_TIMEZONE

MONTH_TABS: dict[int, str] = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}

# Monzo categories that never reach an output (pot top-ups, transfers between
# your own accounts, etc.).
MONZO_API_SKIP_CATEGORIES: set[str] = {"transfers"}

MONZO_BILLS_CATEGORY = "bills"


def is_pot_transfer(t: dict) -> bool:
    # A present-but-null description is valid JSON from Monzo, hence `or ""`.
    return (t.get("description") or "").startswith("pot_")


def is_declined(t: dict) -> bool:
    """True for payments Monzo refused (insufficient funds, blocked card, ...).

    The API returns these alongside successful ones; no money moved, so they
    must never count as spending.
    """
    return bool(t.get("decline_reason"))


def is_filtered_out(t: dict) -> bool:
    """True for transactions deliberately excluded from every output."""
    return (
        t.get("category") in MONZO_API_SKIP_CATEGORIES
        or is_pot_transfer(t)
        or is_declined(t)
    )


def _created_at(t: dict, tz: str = DEFAULT_TIMEZONE) -> date:
    """The calendar date of a transaction in *tz*.

    Monzo timestamps are UTC, so 23:30Z on 30 June is half past midnight on
    1 July in British Summer Time — the date the spender would recognise.
    """
    moment = datetime.fromisoformat(t["created"].replace("Z", "+00:00"))
    return moment.astimezone(ZoneInfo(tz)).date()


def _description(t: dict) -> str:
    merchant = t.get("merchant") or {}
    return merchant.get("name") or t.get("description") or ""


def month_window(year: int, month: int, tz: str = DEFAULT_TIMEZONE) -> tuple[datetime, datetime]:
    """UTC bounds of a calendar month *as lived in* ``tz``.

    The API works in UTC but the user picks a month in their own zone, so during
    British Summer Time a naive UTC window would pull in the first hour of the
    next month and miss the last hour of this one.

    Returns ``(since, before)`` where *before* is exclusive.
    """
    zone = ZoneInfo(tz)
    start = datetime(year, month, 1, tzinfo=zone)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def parse_monzo_transactions(
    transactions: list[dict],
    category_map: dict[str, str] | None = None,
    tz: str = DEFAULT_TIMEZONE,
) -> pd.DataFrame:
    """Spending transactions, excluding bills, transfers, pots and incoming money."""
    mapping = DEFAULT_MONZO_CATEGORY_MAP if category_map is None else category_map
    rows = []
    for t in transactions:
        if is_filtered_out(t) or t.get("category") == MONZO_BILLS_CATEGORY:
            continue
        if t["amount"] >= 0:  # income/refunds handled separately
            continue
        rows.append({
            "Date": _created_at(t, tz),
            "Amount": round(-t["amount"] / 100, 2),
            "Category": mapping.get(t.get("category", ""), ""),
            "Description": _description(t),
        })
    return pd.DataFrame(rows)


def parse_income_transactions(transactions: list[dict], tz: str = DEFAULT_TIMEZONE) -> pd.DataFrame:
    """Incoming money, excluding bill refunds (those belong with the bills)."""
    rows = []
    for t in transactions:
        if is_filtered_out(t) or t.get("category") == MONZO_BILLS_CATEGORY:
            continue
        if t["amount"] <= 0:  # only incoming money
            continue
        rows.append({
            "Date": _created_at(t, tz),
            "Amount": round(t["amount"] / 100, 2),
            "Description": _description(t),
        })
    return pd.DataFrame(rows)


def parse_bill_transactions(transactions: list[dict], tz: str = DEFAULT_TIMEZONE) -> pd.DataFrame:
    """Bill payments, kept separate so they are labelled as bills in the export.

    A refund appears as a negative amount."""
    rows = []
    for t in transactions:
        if t.get("category") != MONZO_BILLS_CATEGORY or is_pot_transfer(t) or is_declined(t):
            continue
        rows.append({
            "Date": _created_at(t, tz),
            "Amount": round(-t["amount"] / 100, 2),
            "Description": _description(t),
        })
    return pd.DataFrame(rows)
