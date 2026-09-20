import io
from datetime import date

import pytest

from bank_statement_exporter import revolut_parser
from bank_statement_exporter.revolut_parser import parse_revolut_csv

HEADER = "Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\n"


def csv_of(*rows: str):
    return io.StringIO(HEADER + "".join(row + "\n" for row in rows))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Keep the module-level caches clean and every HTTP call out of the tests."""
    revolut_parser._rate_cache.clear()
    revolut_parser._rate_failures.clear()
    monkeypatch.setattr(revolut_parser, "_fetch_range", lambda *a, **k: None)
    monkeypatch.setattr(revolut_parser, "_fetch_rate", lambda *a, **k: None)
    yield
    revolut_parser._rate_cache.clear()
    revolut_parser._rate_failures.clear()


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


def test_failed_conversion_is_flagged_rather_than_silently_written():
    expenses, _, _ = parse_revolut_csv(
        csv_of("CARD_PAYMENT,Current,2025-03-02 10:00:00,2025-03-02 11:00:00,Cafe,-10.00,0,EUR,COMPLETED,100")
    )
    assert expenses.loc[0, "Rate Failed"]
    assert "rate unavailable" in expenses.loc[0, "Original"]


def test_a_failed_lookup_is_not_retried_for_every_row(monkeypatch):
    calls = []
    monkeypatch.setattr(revolut_parser, "_fetch_rate",
                        lambda c, d: calls.append((c, d)) or None)
    assert revolut_parser._rate_for("EUR", "2025-03-02") is None
    assert revolut_parser._rate_for("EUR", "2025-03-02") is None
    assert len(calls) == 1


def test_a_failed_lookup_is_retried_once_the_backoff_passes(monkeypatch):
    calls = []
    monkeypatch.setattr(revolut_parser, "_fetch_rate",
                        lambda c, d: calls.append((c, d)) or None)
    assert revolut_parser._rate_for("EUR", "2025-03-02") is None
    # A transient outage must not poison the rate for the life of the process.
    monkeypatch.setattr(revolut_parser, "RATE_RETRY_SECONDS", 0)
    revolut_parser._rate_for("EUR", "2025-03-02")
    assert len(calls) == 2


def test_rates_are_fetched_once_per_currency_not_once_per_row(monkeypatch):
    ranges = []

    def fake_range(currency, start, end):
        ranges.append((currency, start, end))
        return {"2025-03-03": 0.80}

    monkeypatch.setattr(revolut_parser, "_fetch_range", fake_range)
    monkeypatch.setattr(revolut_parser, "_fetch_rate",
                        lambda *a: pytest.fail("should not fall back to per-date lookups"))
    rows = [
        f"CARD_PAYMENT,Current,2025-03-{d:02d} 10:00:00,2025-03-{d:02d} 11:00:00,Shop,-10.00,0,EUR,COMPLETED,1"
        for d in range(3, 9)
    ]
    expenses, _, _ = parse_revolut_csv(csv_of(*rows))
    assert len(expenses) == 6
    assert ranges == [("EUR", "2025-03-03", "2025-03-08")]


def test_a_date_with_no_published_rate_uses_the_previous_one(monkeypatch):
    # Rates are not published at weekends.
    monkeypatch.setattr(revolut_parser, "_fetch_range", lambda *a: {"2025-03-07": 0.50})
    monkeypatch.setattr(revolut_parser, "_fetch_rate", lambda *a: pytest.fail("no fallback expected"))
    expenses, _, _ = parse_revolut_csv(
        csv_of("CARD_PAYMENT,Current,2025-03-09 10:00:00,2025-03-09 11:00:00,Sunday,-10.00,0,EUR,COMPLETED,1")
    )
    assert expenses.loc[0, "Amount"] == 5.00


def test_a_fee_that_swallows_the_whole_credit_is_not_exported_as_minus_zero():
    expenses, income, _ = parse_revolut_csv(
        csv_of("TOPUP,Current,2025-03-02 10:00:00,2025-03-02 11:00:00,All fee,0.50,0.50,GBP,COMPLETED,1")
    )
    assert expenses.empty and income.empty


def test_a_fee_adds_to_an_expense_rather_than_cancelling_it():
    expenses, _, _ = parse_revolut_csv(
        csv_of("CARD_PAYMENT,Current,2025-03-02 10:00:00,2025-03-02 11:00:00,Cash,-0.50,0.50,GBP,COMPLETED,1")
    )
    assert expenses.loc[0, "Amount"] == 1.00


def test_a_fee_is_added_to_the_cost_of_the_transaction():
    expenses, _, _ = parse_revolut_csv(
        csv_of("ATM,Current,2025-03-02 10:00:00,2025-03-02 11:00:00,Cash,-100.00,5.00,GBP,COMPLETED,100")
    )
    assert expenses.loc[0, "Amount"] == 105.00
    assert "5.00 GBP fee" in expenses.loc[0, "Original"]


def test_a_fee_is_deducted_from_money_received():
    _, income, _ = parse_revolut_csv(
        csv_of("TRANSFER,Current,2025-03-03 10:00:00,2025-03-03 11:00:00,Payment in,50.00,1.50,GBP,COMPLETED,150")
    )
    assert income.loc[0, "Amount"] == 48.50


def test_a_foreign_fee_is_folded_in_before_conversion(fixed_rate):
    expenses, _, _ = parse_revolut_csv(
        csv_of("CARD_PAYMENT,Current,2025-03-02 10:00:00,2025-03-02 11:00:00,Cafe,-10.00,2.50,EUR,COMPLETED,100")
    )
    assert expenses.loc[0, "Amount"] == 10.00  # (10.00 + 2.50) * 0.80


def test_one_unreadable_date_does_not_cost_the_rest_of_the_statement():
    expenses, _, filtered = parse_revolut_csv(
        csv_of(
            "CARD_PAYMENT,Current,2025-03-02 10:00:00,,Broken row,-9.99,0,GBP,COMPLETED,100",
            "CARD_PAYMENT,Current,2025-03-03 10:00:00,2025-03-03 11:00:00,Good row,-5.00,0,GBP,COMPLETED,95",
        )
    )
    assert list(expenses["Description"]) == ["Good row"]
    assert filtered[0]["Reason"] == "unreadable date"
    assert filtered[0]["Description"] == "Broken row"


def test_a_row_with_no_amount_is_reported_not_exported():
    expenses, income, filtered = parse_revolut_csv(
        csv_of("CARD_PAYMENT,Current,2025-03-02 10:00:00,2025-03-02 11:00:00,No amount,,0,GBP,COMPLETED,100")
    )
    assert expenses.empty and income.empty
    assert filtered[0]["Reason"] == "no readable amount"


def test_skipped_rows_say_why_they_were_skipped():
    _, _, filtered = parse_revolut_csv(
        csv_of(
            "EXCHANGE,Current,2025-03-04 10:00:00,2025-03-04 11:00:00,Exchanged,-20.00,0,GBP,COMPLETED,130",
            "TRANSFER,Savings,2025-03-05 10:00:00,2025-03-05 11:00:00,Flexible Cash Funds,-10.00,0,GBP,COMPLETED,120",
        )
    )
    assert [f["Reason"] for f in filtered] == ["currency exchange", "internal transfer"]
