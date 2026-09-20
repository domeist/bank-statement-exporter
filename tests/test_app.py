"""End-to-end tests that run the real Streamlit script with the APIs stubbed out."""

import json
import time
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from bank_statement_exporter import config, monzo_api, ui

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

# Dates deliberately run counter to the order the editors render in (expenses,
# then bills, then income) so that the ordering assertions actually bite.
RAW_TRANSACTIONS = [
    {"created": "2025-03-20T10:00:00.000Z", "amount": -1250, "category": "groceries",
     "description": "TESCO", "merchant": {"name": "Tesco"}},
    {"created": "2025-03-05T09:00:00.000Z", "amount": -50000, "category": "bills",
     "description": "RENT", "merchant": None},
    {"created": "2025-03-02T09:00:00.000Z", "amount": 200000, "category": "general",
     "description": "SALARY", "merchant": None},
    {"created": "2025-03-07T09:00:00.000Z", "amount": -900, "category": "transfers",
     "description": "TRANSFER", "merchant": None},
]

FETCHED_MARCH = {"selection": (2025, 3), "raw": RAW_TRANSACTIONS}

MONZO_KEYS = {"monzo_client_id": "client", "monzo_client_secret": "secret"}


@pytest.fixture
def make_config(tmp_path, monkeypatch):
    def _make(**values):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "monzo_token_path": str(tmp_path / "monzo-token.json"),
            **values,
        }))
        monkeypatch.setattr(config, "_CONFIG_PATH", path)
        return path
    return _make


@pytest.fixture
def exported(monkeypatch):
    """Capture the table handed to the download buttons."""
    captured = {}
    real = ui.build_export_frame

    def spy(rows):
        captured["frame"] = real(rows)
        return captured["frame"]

    monkeypatch.setattr(ui, "build_export_frame", spy)
    return captured


@pytest.fixture
def monzo_connected(monkeypatch):
    monkeypatch.setattr(monzo_api, "load_monzo_token", lambda path: {
        "access_token": "tok", "refresh_token": "ref", "expires_at": time.time() + 3600,
    })
    monkeypatch.setattr(monzo_api, "get_accounts", lambda token: [
        {"type": "uk_retail", "id": "acc_1"},
    ])


def run_app(year: int | None = None, month: str | None = None, **session_state):
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    for key, value in session_state.items():
        at.session_state[key] = value
    at.run()
    if year:
        at.selectbox[0].set_value(year).run()
    if month:
        at.selectbox[1].set_value(month).run()
    return at


def march(**session_state):
    """The app showing the fetched March 2025 transactions."""
    return run_app(year=2025, month="Mar", monzo_transactions=FETCHED_MARCH, **session_state)


def click(at, label):
    for button in at.button:
        if label in button.label:
            return button.click().run()
    raise AssertionError(f"no button labelled {label!r} in {[b.label for b in at.button]}")


def labels(at):
    return [b.label for b in at.button] + [b.label for b in at.download_button]


def test_app_runs_with_no_configuration_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_CONFIG_PATH", tmp_path / "missing.json")
    at = run_app()
    assert not at.exception
    assert at.file_uploader  # the Revolut upload is offered with no setup at all
    assert any("Add `monzo_client_id`" in c.value for c in at.caption)


def test_unreadable_config_is_reported_instead_of_crashing(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    monkeypatch.setattr(config, "_CONFIG_PATH", path)
    at = run_app()
    assert not at.exception
    assert "not valid JSON" in at.error[0].value


def test_monzo_section_explains_itself_when_not_configured(make_config):
    make_config()
    at = run_app()
    assert not at.exception
    assert any("Add `monzo_client_id`" in c.value for c in at.caption)


def test_monzo_connect_link_is_shown_when_no_token_is_cached(make_config, monkeypatch):
    make_config(**MONZO_KEYS)
    monkeypatch.setattr(monzo_api, "load_monzo_token", lambda path: None)
    at = run_app()
    assert not at.exception
    assert any("Not connected to Monzo." in w.value for w in at.warning)


def test_fetched_transactions_are_summarised_by_table(make_config, monzo_connected):
    make_config(**MONZO_KEYS)
    at = march()
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "1 transactions" in body and "1 bills" in body and "1 income" in body
    assert "1 filtered out" in body  # the transfer


def test_downloads_are_the_output_of_the_app(make_config, monzo_connected):
    make_config(**MONZO_KEYS)
    at = march()
    assert not at.exception
    assert "Download Excel (.xlsx)" in labels(at)
    assert "Download CSV" in labels(at)
    # Nothing else to click: the download is the only output.
    assert not any("Import" in label for label in labels(at))


def test_the_download_contains_every_reviewed_row_in_date_order(
    make_config, monzo_connected, exported
):
    make_config(**MONZO_KEYS)
    at = march()
    assert not at.exception

    frame = exported["frame"]
    # Rendered expenses-then-bills-then-income; exported oldest first.
    assert list(frame["Description"]) == ["SALARY", "RENT", "Tesco"]
    assert list(frame["Type"]) == ["Income", "Bill", "Expense"]
    assert "TRANSFER" not in list(frame["Description"])  # filtered out
    assert list(frame["Amount"]) == [2000.0, 500.0, 12.5]  # all positive


def test_data_from_another_month_is_not_shown_under_this_months_heading(
    make_config, monzo_connected
):
    make_config(**MONZO_KEYS)
    at = run_app(year=2025, month="Aug", monzo_transactions=FETCHED_MARCH)
    assert not at.exception
    assert any("the fetched data is Mar 2025" in i.value for i in at.info)
    assert not any("Download" in label for label in labels(at))


def test_a_failed_fetch_does_not_leave_the_previous_month_downloadable(
    make_config, monzo_connected, monkeypatch
):
    make_config(**MONZO_KEYS)

    def boom(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(monzo_api, "get_transactions", boom)
    at = click(march(), "Fetch transactions")
    assert not at.exception
    assert "network down" in at.error[0].value
    assert not any("Download" in label for label in labels(at))
    assert "monzo_transactions" not in at.session_state
