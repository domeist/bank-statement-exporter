from datetime import date, datetime, timezone

from bank_statement_exporter.parser import (
    is_declined,
    is_filtered_out,
    is_pot_transfer,
    month_window,
    parse_bill_transactions,
    parse_income_transactions,
    parse_monzo_transactions,
)


def tx(amount, category="general", description="THING", merchant=None, created="2025-03-04T10:00:00.000Z"):
    return {"created": created, "amount": amount, "category": category,
            "description": description, "merchant": merchant}


def test_expense_is_stored_positive_with_mapped_category():
    df = parse_monzo_transactions([tx(-1250, "groceries", merchant={"name": "Tesco"})])
    assert df.loc[0, "Amount"] == 12.50
    assert df.loc[0, "Category"] == "Groceries"
    assert df.loc[0, "Description"] == "Tesco"
    assert df.loc[0, "Date"] == date(2025, 3, 4)


def test_unmapped_category_is_left_blank_for_manual_entry():
    df = parse_monzo_transactions([tx(-500, "unknown_thing")])
    assert df.loc[0, "Category"] == ""


def test_custom_category_map_is_used():
    df = parse_monzo_transactions([tx(-500, "groceries")], {"groceries": "Food"})
    assert df.loc[0, "Category"] == "Food"


def test_transfers_pots_bills_and_income_are_excluded_from_expenses():
    transactions = [
        tx(-100, "transfers"),
        tx(-100, "general", description="pot_123"),
        tx(-100, "bills"),
        tx(+100, "general"),
    ]
    assert parse_monzo_transactions(transactions).empty


def test_income_is_kept_positive():
    df = parse_income_transactions([tx(200000, "general", description="SALARY")])
    assert df.loc[0, "Amount"] == 2000.00
    assert df.loc[0, "Description"] == "SALARY"


def test_bill_refund_is_reported_as_a_bill_not_as_income():
    transactions = [tx(2500, "bills", description="ENERGY REFUND")]
    assert parse_income_transactions(transactions).empty
    bills = parse_bill_transactions(transactions)
    assert bills.loc[0, "Amount"] == -25.00  # a refund stays negative


def test_bills_are_positive():
    df = parse_bill_transactions([tx(-50000, "bills", description="RENT")])
    assert df.loc[0, "Amount"] == 500.00


def test_pot_transfer_detection():
    assert is_pot_transfer({"description": "pot_00009"})
    assert not is_pot_transfer({"description": "TESCO"})
    assert not is_pot_transfer({})


def test_declined_payments_never_count_as_spending():
    declined = tx(-5000, "eating_out", description="RESTAURANT")
    declined["decline_reason"] = "INSUFFICIENT_FUNDS"
    assert parse_monzo_transactions([declined]).empty
    assert is_declined(declined) and is_filtered_out(declined)


def test_a_declined_bill_is_not_recorded_as_paid():
    declined = tx(-50000, "bills", description="RENT")
    declined["decline_reason"] = "CARD_BLOCKED"
    assert parse_bill_transactions([declined]).empty


def test_a_declined_credit_is_not_recorded_as_income():
    declined = tx(200000, "general", description="SALARY")
    declined["decline_reason"] = "OTHER"
    assert parse_income_transactions([declined]).empty


def test_a_successful_transaction_has_no_decline_reason():
    assert not is_declined(tx(-500, "groceries"))
    assert not is_declined({"description": "TESCO", "decline_reason": None})


def test_the_month_window_covers_the_month_as_lived_in_the_local_zone():
    since, before = month_window(2025, 3, "Europe/London")
    # 1 March 00:00 London is 00:00 UTC; 1 April 00:00 BST is 31 March 23:00 UTC.
    assert since.strftime("%Y-%m-%dT%H:%M") == "2025-03-01T00:00"
    assert before.strftime("%Y-%m-%dT%H:%M") == "2025-03-31T23:00"


def test_a_late_night_purchase_is_fetched_by_the_month_it_belongs_to():
    created = datetime(2025, 3, 31, 23, 30, tzinfo=timezone.utc)  # 00:30 on 1 April, BST
    march_since, march_before = month_window(2025, 3, "Europe/London")
    april_since, april_before = month_window(2025, 4, "Europe/London")
    assert not (march_since <= created < march_before)
    assert april_since <= created < april_before


def test_the_window_wraps_the_year():
    since, before = month_window(2025, 12, "Europe/London")
    assert since.year == 2025 and before.year == 2026


def test_utc_can_still_be_requested():
    since, before = month_window(2025, 3, "UTC")
    assert before.strftime("%Y-%m-%dT%H:%M") == "2025-04-01T00:00"
