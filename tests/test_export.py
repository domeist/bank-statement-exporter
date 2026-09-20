import io
from datetime import date

import pandas as pd

from bank_statement_exporter.export import (
    EXPORT_COLUMNS,
    build_export_frame,
    suggested_filename,
    to_csv_bytes,
    to_excel_bytes,
)
from bank_statement_exporter.model import BILL, EXPENSE, INCOME, MONZO, REVOLUT, transaction

ROWS = [
    transaction(date=date(2025, 3, 4), amount=12.5, description="Tesco",
                category="Groceries", kind=EXPENSE, source=MONZO, trip="Lisbon"),
    transaction(date=date(2025, 3, 5), amount=900.0, description="RENT",
                category="Bills", kind=BILL, source=MONZO),
    transaction(date=date(2025, 3, 6), amount=2000.0, description="SALARY",
                kind=INCOME, source=REVOLUT),
]


def test_export_columns_are_self_describing():
    frame = build_export_frame(ROWS)
    assert list(frame.columns) == EXPORT_COLUMNS
    assert EXPORT_COLUMNS == ["Date", "Description", "Category", "Amount", "Type", "Source", "Trip", "Note"]


def test_every_row_is_exported_with_its_type_and_source():
    frame = build_export_frame(ROWS)
    assert list(frame["Type"]) == ["Expense", "Bill", "Income"]
    assert list(frame["Source"]) == ["Monzo", "Monzo", "Revolut"]
    assert list(frame["Amount"]) == [12.5, 900.0, 2000.0]  # always positive
    assert frame.loc[0, "Trip"] == "Lisbon"


def test_an_empty_selection_still_produces_a_valid_table():
    frame = build_export_frame([])
    assert list(frame.columns) == EXPORT_COLUMNS and frame.empty


def test_csv_round_trips():
    data = to_csv_bytes(build_export_frame(ROWS))
    reloaded = pd.read_csv(io.BytesIO(data))
    assert list(reloaded["Description"]) == ["Tesco", "RENT", "SALARY"]
    assert reloaded.loc[0, "Date"] == "2025-03-04"


def test_excel_round_trips_with_real_dates_and_numbers():
    data = to_excel_bytes(build_export_frame(ROWS))
    assert data[:2] == b"PK"  # a real xlsx container
    reloaded = pd.read_excel(io.BytesIO(data))
    assert reloaded.loc[0, "Date"].date() == date(2025, 3, 4)
    assert reloaded.loc[2, "Amount"] == 2000.0


def test_filename_names_the_month_it_covers():
    assert suggested_filename(ROWS, "xlsx") == "transactions-2025-03.xlsx"


def test_filename_spans_a_range_when_the_rows_cross_months():
    rows = ROWS + [transaction(date=date(2025, 4, 2), amount=1.0, description="x",
                               kind=EXPENSE, source=MONZO)]
    assert suggested_filename(rows, "csv") == "transactions-2025-03-04-to-2025-04-02.csv"


def test_filename_falls_back_when_there_is_nothing_to_date():
    assert suggested_filename([], "csv") == "transactions.csv"


def test_a_conversion_warning_reaches_the_exported_file():
    rows = [transaction(date=date(2025, 3, 2), amount=250.0, description="Hotel Berlin",
                        kind=EXPENSE, source=REVOLUT, note="⚠️ EUR -250.00 (rate unavailable)")]
    assert "rate unavailable" in build_export_frame(rows).loc[0, "Note"]


def test_a_formula_in_a_description_cannot_execute_in_a_spreadsheet():
    # Payment references are chosen by whoever sent the money.
    payloads = ["=cmd|'/c calc'!A1", "+1+1", "-1+1", "@SUM(A1)", "\tTab", "\rCR"]
    rows = [transaction(date=date(2025, 3, 2), amount=1.0, description=p,
                        kind=EXPENSE, source=REVOLUT) for p in payloads]
    exported = list(build_export_frame(rows)["Description"])
    assert all(cell.startswith("'") for cell in exported), exported


def test_ordinary_descriptions_are_left_alone():
    rows = [transaction(date=date(2025, 3, 2), amount=1.0, description="Tesco Metro",
                        kind=EXPENSE, source=REVOLUT)]
    assert build_export_frame(rows).loc[0, "Description"] == "Tesco Metro"


def test_the_formula_guard_also_covers_category_trip_and_note():
    rows = [transaction(date=date(2025, 3, 2), amount=1.0, description="ok", category="=A1",
                        trip="=B2", note="=C3", kind=EXPENSE, source=REVOLUT)]
    frame = build_export_frame(rows)
    assert frame.loc[0, "Category"].startswith("'")
    assert frame.loc[0, "Trip"].startswith("'")
    assert frame.loc[0, "Note"].startswith("'")
