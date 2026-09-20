from datetime import date

from monzo_importer.parser import (
    is_pot_transfer,
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
