"""Parsing of Revolut CSV statement exports, with conversion to GBP."""

import math
import time
from datetime import datetime

import pandas as pd
import requests

# Revolut writes the Type column in upper case (CARD_PAYMENT, EXCHANGE, ...).
# Compared case-insensitively so a change of casing in a future export format
# does not silently disable the filter.
REVOLUT_SKIP_TYPES: set[str] = {"exchange"}

# Descriptions marking Revolut-internal movements (savings pots and similar).
REVOLUT_INTERNAL_DESCRIPTIONS: tuple[str, ...] = ("Flexible Cash Funds",)

# api.frankfurter.app now 301-redirects here; point at the current host directly.
RATE_API_URL = "https://api.frankfurter.dev/v1"
RATE_UNAVAILABLE_NOTE = "rate unavailable"
RATE_TIMEOUT = 10
# A failed lookup is retried after this long, so one blip is not permanent.
RATE_RETRY_SECONDS = 60

# Module-level caches so exchange rates aren't re-fetched within a session.
_rate_cache: dict[tuple[str, str], float] = {}
_rate_failures: dict[tuple[str, str], float] = {}


def _to_float(value: object) -> float | None:
    """A usable number, or None for blanks, text and NaN/inf."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _parse_date(value: object) -> datetime | None:
    """Parse the date half of a Revolut timestamp, or None if it is unusable.

    Deliberately naive: a statement row carries a calendar date, not an instant,
    and the time zone of the original purchase is not in the file.
    """
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")  # noqa: DTZ007
    except ValueError:
        return None


def _skipped_row(row, reason: str, amount: object = "") -> dict:
    """A row that will not be exported, described for the user."""
    return {
        "Date": str(row["Completed Date"])[:10],
        "Amount": amount,
        "Currency": str(row["Currency"]),
        "Type": str(row["Type"]),
        "Description": str(row["Description"]),
        "Reason": reason,
    }


def _is_internal(description: str) -> bool:
    """True for Revolut-internal movements (savings pots) that aren't real transactions."""
    return any(marker in description for marker in REVOLUT_INTERNAL_DESCRIPTIONS)


def _is_skipped_type(tx_type: str) -> bool:
    return tx_type.strip().lower() in REVOLUT_SKIP_TYPES


def _fetch_rate(currency: str, date_str: str) -> float | None:
    """The GBP rate for one date, or None if it cannot be fetched."""
    try:
        resp = requests.get(
            f"{RATE_API_URL}/{date_str}",
            params={"from": currency, "to": "GBP"},
            timeout=RATE_TIMEOUT,
        )
        resp.raise_for_status()
        return float(resp.json()["rates"]["GBP"])
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


def _fetch_range(currency: str, start: str, end: str) -> dict[str, float] | None:
    """Every published GBP rate between two dates, in one request."""
    try:
        resp = requests.get(
            f"{RATE_API_URL}/{start}..{end}",
            params={"from": currency, "to": "GBP"},
            timeout=RATE_TIMEOUT,
        )
        resp.raise_for_status()
        series = resp.json()["rates"]
        return {day: float(rates["GBP"]) for day, rates in series.items()}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


def _nearest_published(series: dict[str, float], date_str: str) -> float | None:
    """The rate for *date_str*, or the most recent one before it.

    Rates are not published at weekends or on holidays, which is what the
    single-date endpoint does for us server-side.
    """
    if date_str in series:
        return series[date_str]
    earlier = [day for day in series if day <= date_str]
    return series[max(earlier)] if earlier else None


def prefetch_rates(currency: str, dates: set[str]) -> None:
    """Fill the cache for one currency in a single request.

    Without this, a month of foreign spending means one blocking HTTP call per
    day, serially.
    """
    wanted = {d for d in dates if (d, currency) not in _rate_cache}
    if currency == "GBP" or not wanted:
        return
    series = _fetch_range(currency, min(wanted), max(wanted))
    if not series:
        return
    for day in wanted:
        rate = _nearest_published(series, day)
        if rate is not None:
            _rate_cache[(day, currency)] = rate


def _rate_for(currency: str, date_str: str) -> float | None:
    key = (date_str, currency)
    if key in _rate_cache:
        return _rate_cache[key]
    failed_at = _rate_failures.get(key)
    if failed_at is not None and time.monotonic() - failed_at < RATE_RETRY_SECONDS:
        return None  # recently unavailable; don't hammer the API for every row
    rate = _fetch_rate(currency, date_str)
    if rate is None:
        _rate_failures[key] = time.monotonic()
        return None
    _rate_cache[key] = rate
    _rate_failures.pop(key, None)
    return rate


def _to_gbp(amount: float, currency: str, date_str: str) -> tuple[float, str, bool]:
    """Convert *amount* to GBP using the historical ECB rate for *date_str* (YYYY-MM-DD).

    Returns ``(gbp_amount, original_note, converted)``. When the rate cannot be
    fetched the original amount is returned unchanged, the note flags it and
    ``converted`` is False — callers must not write such a row to the sheet
    without the user correcting the amount first.
    """
    if currency == "GBP":
        return amount, "", True
    rate = _rate_for(currency, date_str)
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
    * Fees are added to the amount they were charged on.
    * A row that cannot be read is reported in ``filtered_out`` with a reason
      rather than failing the whole statement.
    """
    raw = pd.read_csv(file)
    expenses: list[dict] = []
    income: list[dict] = []
    filtered_out: list[dict] = []

    pending: list[dict] = []
    for _, row in raw.iterrows():
        if str(row["State"]) != "COMPLETED":
            continue

        description = str(row["Description"])
        currency = str(row["Currency"])
        tx_type = str(row["Type"])

        amount = _to_float(row["Amount"])
        if amount is None:
            filtered_out.append(_skipped_row(row, "no readable amount"))
            continue
        if amount == 0.0:
            continue

        completed = _parse_date(row["Completed Date"])
        if completed is None:
            # One bad row must not cost the user the rest of the statement.
            filtered_out.append(_skipped_row(row, "unreadable date", amount))
            continue

        if _is_skipped_type(tx_type) or _is_internal(description):
            reason = "currency exchange" if _is_skipped_type(tx_type) else "internal transfer"
            filtered_out.append(_skipped_row(row, reason, amount))
            continue

        # Fees are charged on top of the amount, so fold them in before converting.
        fee = _to_float(row["Fee"]) if "Fee" in raw.columns else None
        net = round(amount - fee, 2) if fee else amount
        if net == 0.0:  # a fee that exactly cancels the amount
            continue

        pending.append({
            "date": completed.date(),
            "date_str": completed.strftime("%Y-%m-%d"),
            "description": description,
            "currency": currency,
            "net": net,
            "fee": fee,
        })

    # One request per foreign currency, rather than one per row.
    by_currency: dict[str, set[str]] = {}
    for item in pending:
        if item["currency"] != "GBP":
            by_currency.setdefault(item["currency"], set()).add(item["date_str"])
    for currency, dates in by_currency.items():
        prefetch_rates(currency, dates)

    for item in pending:
        net, currency, fee = item["net"], item["currency"], item["fee"]
        gbp_amount, note, converted = _to_gbp(net, currency, item["date_str"])
        if fee:
            charged = "incl." if net < 0 else "less"
            fee_note = f"{charged} {abs(fee):.2f} {currency} fee"
            note = f"{note}, {fee_note}" if note else fee_note

        if net > 0:
            income.append({
                "Date": item["date"],
                "Amount": gbp_amount,
                "Description": item["description"],
                "Original": note,
                "Rate Failed": not converted,
            })
        else:
            expenses.append({
                "Date": item["date"],
                # Revolut writes expenses as negative; amounts are always kept
                # positive internally, so flip the sign here.
                "Amount": round(-gbp_amount, 2),
                "Category": "",
                "Description": item["description"],
                "Original": note,
                "Rate Failed": not converted,
            })

    return (
        pd.DataFrame(expenses) if expenses else pd.DataFrame(),
        pd.DataFrame(income) if income else pd.DataFrame(),
        filtered_out,
    )
