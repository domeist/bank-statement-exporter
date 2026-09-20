"""Streamlit rendering helpers shared by the Monzo and Revolut sections."""

import pandas as pd
import streamlit as st

from .export import build_export_frame, suggested_filename, to_csv_bytes, to_excel_bytes
from .model import BILL, EXPENSE, INCOME, transaction

_DATE_COLUMN = st.column_config.DateColumn(label="Date", format="YYYY-MM-DD")
_AMOUNT_COLUMN = st.column_config.NumberColumn(label="Amount", format="£%.2f")
_ORIGINAL_COLUMN = st.column_config.TextColumn(label="Original", disabled=True)


def render_summary(counts: dict[str, int], suffix: str = "") -> None:
    parts = [f"**{value} {label}**" for label, value in counts.items() if value]
    if parts:
        st.write(" + ".join(parts) + (f" ({suffix})" if suffix else ""))


def render_filtered_out(filtered_out: list[dict]) -> None:
    if not filtered_out:
        return
    with st.expander(f"Show {len(filtered_out)} filtered-out transactions"):
        st.dataframe(filtered_out, width="stretch", hide_index=True)


def _warn_blank_categories(edited: pd.DataFrame) -> None:
    blank_count = int((edited["Category"] == "").sum())
    if blank_count:
        st.caption(f"{blank_count} transaction(s) have no category assigned.")


def render_expense_editor(
    df: pd.DataFrame,
    *,
    categories: list[str],
    key_prefix: str,
    source: str,
    caption: str = "",
) -> list[dict]:
    """Render the editable expenses table and return canonical transactions."""
    trip_name: str = st.text_input("Trip name (optional)", value="", key=f"{key_prefix}_trip")
    if caption:
        st.caption(caption)

    columns = ["Date", "Amount", "Category", "Description"]
    column_config = {
        "Date": _DATE_COLUMN,
        "Amount": _AMOUNT_COLUMN,
        "Category": st.column_config.SelectboxColumn(label="Category", options=categories),
    }
    if "Original" in df.columns:
        columns.append("Original")
        column_config["Original"] = _ORIGINAL_COLUMN

    edited = st.data_editor(
        df[columns],
        column_config=column_config,
        width="stretch",
        hide_index=True,
        key=f"{key_prefix}_expenses_editor",
    )
    _warn_blank_categories(edited)

    return [
        transaction(
            date=row["Date"], amount=row["Amount"], description=row["Description"],
            category=row["Category"], trip=trip_name, kind=EXPENSE, source=source,
        )
        for _, row in edited.iterrows()
    ]


def render_income_editor(
    df: pd.DataFrame,
    *,
    key_prefix: str,
    source: str,
) -> list[dict]:
    """Render the editable income table and return canonical transactions."""
    st.subheader("Income")
    return _render_simple_editor(df, key_prefix=key_prefix, name="income", source=source, kind=INCOME)


def render_bill_editor(
    df: pd.DataFrame,
    *,
    key_prefix: str,
    source: str,
) -> list[dict]:
    """Render the editable bills table and return canonical transactions."""
    st.subheader("Bills")
    st.caption("Kept separate from other spending so they are labelled as bills in the download.")
    return _render_simple_editor(
        df, key_prefix=key_prefix, name="bills", source=source, kind=BILL, category="Bills"
    )


def _render_simple_editor(
    df: pd.DataFrame,
    *,
    key_prefix: str,
    name: str,
    source: str,
    kind: str,
    category: str = "",
) -> list[dict]:
    columns = ["Date", "Amount", "Description"]
    column_config = {"Date": _DATE_COLUMN, "Amount": _AMOUNT_COLUMN}
    if "Original" in df.columns:
        columns.append("Original")
        column_config["Original"] = _ORIGINAL_COLUMN

    edited = st.data_editor(
        df[columns],
        column_config=column_config,
        width="stretch",
        hide_index=True,
        key=f"{key_prefix}_{name}_editor",
    )
    return [
        transaction(
            date=row["Date"], amount=row["Amount"], description=row["Description"],
            category=category, kind=kind, source=source,
        )
        for _, row in edited.iterrows()
    ]


def render_download_buttons(rows: list[dict]) -> None:
    """Offer the reviewed transactions as a spreadsheet file."""
    frame = build_export_frame(rows)
    st.caption(f"{len(frame)} row(s) ready to download.")
    excel, csv = st.columns(2)
    with excel:
        st.download_button(
            "Download Excel (.xlsx)",
            data=to_excel_bytes(frame),
            file_name=suggested_filename(rows, "xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    with csv:
        st.download_button(
            "Download CSV",
            data=to_csv_bytes(frame),
            file_name=suggested_filename(rows, "csv"),
            mime="text/csv",
            width="stretch",
        )
