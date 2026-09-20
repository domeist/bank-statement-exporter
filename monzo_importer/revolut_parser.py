"""Parsing of Revolut CSV statement exports, with conversion to GBP."""

from datetime import datetime

import pandas as pd
import requests

# Revolut writes the Type column in upper case (CARD_PAYMENT, EXCHANGE, ...).
# Compared case-insensitively so a change of casing in a future export format
# does not silently disable the filter.
REVOLUT_SKIP_TYPES: set[str] = {"exchange"}

# Descriptions marking Revolut-internal movements (savings pots and similar).
REVOLUT_INTERNAL_DESCRIPTIONS: tuple[str, ...] = ("Flexible Cash Funds",)

RATE_API_URL = "https://api.frankfurter.app"
RATE_UNAVAILABLE_NOTE = "rate unavailable"

# Module-level cache so exchange rates aren't re-fetched within a session.
_rate_cache: dict[tuple[str, str], float | None] = {}


def _is_internal(description: str) -> bool:
    """True for Revolut-internal movements (savings pots) that aren't real transactions."""
    return any(marker in description for marker in REVOLUT_INTERNAL_DESCRIPTIONS)


def _is_skipped_type(tx_type: str) -> bool:
    return tx_type.strip().lower() in REVOLUT_SKIP_TYPES


def _fetch_rate(currency: str, date_str: str) -> float | None:
    key = (date_str, currency)
    if key not in _rate_cache:
        try:
            resp = requests.get(
                f"{RATE_API_URL}/{date_str}",
                params={"from": currency, "to": "GBP"},
                timeout=5,
            )
            resp.raise_for_status()
            _rate_cache[key] = float(resp.json()["rates"]["GBP"])
        except (requests.RequestException, ValueError, KeyError, TypeError):
            _rate_cache[key] = None
    return _rate_cache[key]


def _to_gbp(amount: float, currency: str, date_str: str) -> tuple[float, str, bool]:
    """Convert *amount* to GBP using the historical ECB rate for *date_str* (YYYY-MM-DD).

    Returns ``(gbp_amount, original_note, converted)``. When the rate cannot be
    fetched the original amount is returned unchanged, the note flags it and
    ``converted`` is False — callers must not write such a row to the sheet
    without the user correcting the amount first.
    """
    if currency == "GBP":
        return amount, "", True
    rate = _fetch_rate(currency, date_str)
    if rate is None:
        return amount, f"⚠️ {currency} {amount:+.2f} ({RATE_UNAVAILABLE_NOTE})", False
    return round(amount * rate, 2), f"{currency} {amount:+.2f}", True


def parse_revolut_csv(file) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Parse a Revolut CSV statement export.

    Returns ``(expenses_df, income_df, filtered_out)``.

    * All amounts are converted to GBP; the original currency + amount is
      preserved in the ``Original`` column, and ``Rate Failed`` marks rows whose
      conversion could not be done.
    * ``EXCHANGE`` transactions and Flexible Cash Funds movements are filtered out.
    * Only ``COMPLETED`` rows are processed.
    """
    raw = pd.read_csv(file)
    expenses: list[dict] = []
    income: list[dict] = []
    filtered_out: list[dict] = []

    for _, row in raw.iterrows():
        if str(row["State"]) != "COMPLETED":
            continue
        amount = float(row["Amount"])
        if amount == 0.0:
            continue

        description = str(row["Description"])
        currency = str(row["Currency"])
        tx_type = str(row["Type"])
        completed = datetime.strptime(str(row["Completed Date"])[:10], "%Y-%m-%d").date()

        if _is_skipped_type(tx_type) or _is_internal(description):
            filtered_out.append({
                "Date": completed.strftime("%d/%m/%Y"),
                "Amount": amount,
                "Currency": currency,
                "Type": tx_type,
                "Description": description,
            })
            continue

        gbp_amount, note, converted = _to_gbp(amount, currency, completed.strftime("%Y-%m-%d"))

        if amount > 0:
            income.append({
                "Date": completed,
                "Amount": gbp_amount,
                "Description": description,
                "Original": note,
                "Rate Failed": not converted,
            })
        else:
            expenses.append({
                "Date": completed,
                # Revolut writes expenses as negative; amounts are always kept
                # positive internally, so flip the sign here.
                "Amount": round(-gbp_amount, 2),
                "Category": "",
                "Description": description,
                "Original": note,
                "Rate Failed": not converted,
            })

    return (
        pd.DataFrame(expenses) if expenses else pd.DataFrame(),
        pd.DataFrame(income) if income else pd.DataFrame(),
        filtered_out,
    )
