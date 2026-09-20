import io
from datetime import date

import pytest

from monzo_importer import revolut_parser
from monzo_importer.revolut_parser import parse_revolut_csv

HEADER = "Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\n"


def csv_of(*rows: str):
    return io.StringIO(HEADER + "".join(row + "\n" for row in rows))


@pytest.fixture(autouse=True)
def clear_rate_cache():
    """Keep the module-level rate cache from leaking between tests."""
    revolut_parser._rate_cache.clear()
    yield
    revolut_parser._rate_cache.clear()


@pytest.fixture
def fixed_rate(monkeypatch):
    """Pin the FX rate so tests never hit the network."""
    monkeypatch.setattr(revolut_parser, "_fetch_rate", lambda currency, date_str: 0.80)


def test_expense_is_flipped_to_positive_to_match_the_sheet():
    expenses, _, _ = parse_revolut_csv(
        csv_of("CARD_PAYMENT,Current,2025-03-02 10:00:00,2025-03-02 11:00:00,Shop,-12.50,0,GBP,COMPLETED,100")
    )
    assert expenses.loc[0, "Amount"] == 12.50
    assert expenses.loc[0, "Date"] == date(2025, 3, 2)


def test_foreign_expense_is_converted_and_keeps_the_original(fixed_rate):
    expenses, _, _ = parse_revolut_csv(
        csv_of("CARD_PAYMENT,Current,2025-03-02 10:00:00,2025-03-02 11:00:00,Cafe,-10.00,0,EUR,COMPLETED,100")
    )
    assert expenses.loc[0, "Amount"] == 8.00
    assert expenses.loc[0, "Original"] == "EUR -10.00"
    assert not expenses.loc[0, "Rate Failed"]


def test_uppercase_exchange_rows_are_filtered_out():
    expenses, income, filtered = parse_revolut_csv(
        csv_of("EXCHANGE,Current,2025-03-04 10:00:00,2025-03-04 11:00:00,Exchanged to EUR,-20.00,0,GBP,COMPLETED,130")
    )
    assert expenses.empty and income.empty
    assert filtered[0]["Type"] == "EXCHANGE"


def test_internal_savings_movements_are_filtered_out():
    _, _, filtered = parse_revolut_csv(
        csv_of("TRANSFER,Savings,2025-03-05 10:00:00,2025-03-05 11:00:00,Flexible Cash Funds,-10.00,0,GBP,COMPLETED,120")
    )
    assert len(filtered) == 1


def test_pending_and_zero_rows_are_ignored():
    expenses, income, filtered = parse_revolut_csv(
        csv_of(
            "CARD_PAYMENT,Current,2025-03-06 10:00:00,,Pending,-5.00,0,GBP,PENDING,115",
            "CARD_PAYMENT,Current,2025-03-07 10:00:00,2025-03-07 11:00:00,Zero,0.00,0,GBP,COMPLETED,115",
        )
    )
    assert expenses.empty and income.empty and filtered == []


def test_income_row_is_positive():
    _, income, _ = parse_revolut_csv(
        csv_of("TOPUP,Current,2025-03-03 10:00:00,2025-03-03 11:00:00,Top-Up,50.00,0,GBP,COMPLETED,150")
    )
    assert income.loc[0, "Amount"] == 50.00
    assert income.loc[0, "Description"] == "Top-Up"


def test_failed_conversion_is_flagged_rather_than_silently_written(monkeypatch):
    monkeypatch.setattr(revolut_parser, "_fetch_rate", lambda currency, date_str: None)
    expenses, _, _ = parse_revolut_csv(
        csv_of("CARD_PAYMENT,Current,2025-03-02 10:00:00,2025-03-02 11:00:00,Cafe,-10.00,0,EUR,COMPLETED,100")
    )
    assert expenses.loc[0, "Rate Failed"]
    assert "rate unavailable" in expenses.loc[0, "Original"]


def test_a_failed_rate_lookup_is_cached_rather_than_retried(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(url)
        raise revolut_parser.requests.RequestException("offline")

    monkeypatch.setattr(revolut_parser.requests, "get", fake_get)
    assert revolut_parser._fetch_rate("EUR", "2025-03-02") is None
    assert revolut_parser._fetch_rate("EUR", "2025-03-02") is None
    assert len(calls) == 1
