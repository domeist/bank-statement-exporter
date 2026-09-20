from datetime import date

from monzo_importer.model import (
    BILL,
    EXPENSE,
    INCOME,
    MONZO,
    REVOLUT,
    of_kind,
    sort_transactions,
    transaction,
)


def tx(day, kind=EXPENSE, source=MONZO, description="x"):
    return transaction(date=date(2025, 3, day), amount=1.0, description=description,
                       kind=kind, source=source)


def test_rows_are_ordered_by_date_first():
    ordered = sort_transactions([tx(9), tx(2), tx(21)])
    assert [r["date"].day for r in ordered] == [2, 9, 21]


def test_within_a_day_expenses_come_before_bills_and_income():
    ordered = sort_transactions([tx(3, INCOME), tx(3, BILL), tx(3, EXPENSE)])
    assert [r["kind"] for r in ordered] == [EXPENSE, BILL, INCOME]


def test_monzo_comes_before_revolut_within_the_same_day_and_kind():
    ordered = sort_transactions([tx(3, source=REVOLUT, description="r"), tx(3, description="m")])
    assert [r["description"] for r in ordered] == ["m", "r"]


def test_amounts_stay_positive_and_kind_carries_the_meaning():
    row = transaction(date=date(2025, 3, 1), amount=12.5, description="Tesco",
                      kind=EXPENSE, source=MONZO)
    assert row["amount"] == 12.5 and row["kind"] == EXPENSE
    assert row["category"] == "" and row["trip"] == ""


def test_of_kind_filters():
    rows = [tx(1), tx(2, INCOME), tx(3, BILL)]
    assert [r["kind"] for r in of_kind(rows, EXPENSE, BILL)] == [EXPENSE, BILL]
