"""Export transactions to a spreadsheet file."""

import io
from datetime import date

import pandas as pd

from .model import BILL, EXPENSE, INCOME

EXPORT_COLUMNS = ["Date", "Description", "Category", "Amount", "Type", "Source", "Trip"]

_TYPE_LABELS = {EXPENSE: "Expense", INCOME: "Income", BILL: "Bill"}

_SHEET_NAME = "Transactions"
_COLUMN_WIDTHS = {"Date": 12, "Description": 38, "Category": 18, "Amount": 12,
                  "Type": 10, "Source": 10, "Trip": 14}


def build_export_frame(rows: list[dict]) -> pd.DataFrame:
    """Tidy, self-describing table of transactions.

    Amounts stay positive; the Type column says whether a row is money out or in.
    """
    return pd.DataFrame(
        [
            {
                "Date": row["date"],
                "Description": row["description"],
                "Category": row["category"],
                "Amount": row["amount"],
                "Type": _TYPE_LABELS.get(row["kind"], row["kind"].title()),
                "Source": row["source"],
                "Trip": row["trip"],
            }
            for row in rows
        ],
        columns=EXPORT_COLUMNS,
    )


def to_csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")


def to_excel_bytes(frame: pd.DataFrame) -> bytes:
    """An .xlsx file with a frozen header, sized columns and real date/number cells."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl", date_format="YYYY-MM-DD") as writer:
        frame.to_excel(writer, sheet_name=_SHEET_NAME, index=False)
        worksheet = writer.sheets[_SHEET_NAME]
        worksheet.freeze_panes = "A2"
        for index, column in enumerate(frame.columns, start=1):
            letter = worksheet.cell(row=1, column=index).column_letter
            worksheet.column_dimensions[letter].width = _COLUMN_WIDTHS.get(column, 14)
        amount_column = frame.columns.get_loc("Amount") + 1
        for row_number in range(2, len(frame) + 2):
            worksheet.cell(row=row_number, column=amount_column).number_format = "0.00"
    return buffer.getvalue()


def suggested_filename(rows: list[dict], extension: str) -> str:
    """Name the download after the month it covers, e.g. transactions-2025-03.csv."""
    dates = [row["date"] for row in rows if isinstance(row["date"], date)]
    if not dates:
        return f"transactions.{extension}"
    first, last = min(dates), max(dates)
    if (first.year, first.month) == (last.year, last.month):
        return f"transactions-{first:%Y-%m}.{extension}"
    return f"transactions-{first:%Y-%m-%d}-to-{last:%Y-%m-%d}.{extension}"
