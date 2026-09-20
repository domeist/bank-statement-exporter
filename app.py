"""Streamlit UI for turning Monzo and Revolut transactions into a spreadsheet.

Monzo is optional: with no configuration at all you can still upload a Revolut
CSV and download a tidy Excel or CSV file.
"""

import secrets
import time
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path

import requests
import streamlit as st

from bank_statement_exporter import ui
from bank_statement_exporter.config import ConfigError, load_config
from bank_statement_exporter.model import MONZO, REVOLUT, sort_transactions
from bank_statement_exporter.monzo_api import (
    MonzoSCARequired,
    clear_oauth_state,
    exchange_code,
    get_accounts,
    get_auth_url,
    get_transactions,
    load_monzo_token,
    load_oauth_state,
    refresh_monzo_token,
    save_monzo_token,
    save_oauth_state,
)
from bank_statement_exporter.parser import (
    MONTH_TABS,
    is_declined,
    is_filtered_out,
    is_pot_transfer,
    parse_bill_transactions,
    parse_income_transactions,
    parse_monzo_transactions,
)
from bank_statement_exporter.revolut_parser import parse_revolut_csv

st.set_page_config(page_title="Bank Statement Exporter", page_icon="💸")
st.title("Bank Statement Exporter")

try:
    cfg = load_config()
except ConfigError as exc:
    st.error(str(exc))
    st.stop()

# ── Monzo helpers & OAuth callback ───────────────────────────────────────────


def _clear_monzo_token() -> None:
    st.session_state.pop("monzo_token", None)
    Path(cfg.monzo_token_path).unlink(missing_ok=True)


def _load_or_refresh_token() -> str | None:
    saved = load_monzo_token(cfg.monzo_token_path)
    if not saved:
        return None
    if time.time() < saved["expires_at"] - 60:
        return saved["access_token"]
    try:
        token_data = refresh_monzo_token(cfg.monzo_client_id, cfg.monzo_client_secret, saved["refresh_token"])
    except requests.RequestException as exc:
        st.warning(f"Failed to refresh Monzo token: {exc}")
        return None
    save_monzo_token(token_data, cfg.monzo_token_path)
    return token_data["access_token"]


params = st.query_params
if cfg.monzo_enabled and "code" in params and "monzo_token" not in st.session_state:
    expected_state = load_oauth_state(cfg.monzo_token_path)
    if expected_state is None or params.get("state") != expected_state:
        st.error("OAuth state mismatch — possible CSRF or stale link. Please try connecting again.")
        st.query_params.clear()
        st.stop()
    clear_oauth_state(cfg.monzo_token_path)
    try:
        token_data = exchange_code(
            cfg.monzo_client_id, cfg.monzo_client_secret, params["code"], cfg.redirect_uri
        )
    except requests.RequestException as exc:
        st.query_params.clear()
        st.error(f"Failed to authenticate with Monzo: {exc}")
        st.stop()
    save_monzo_token(token_data, cfg.monzo_token_path)
    st.session_state["monzo_token"] = token_data["access_token"]
    st.session_state["monzo_needs_sca"] = True
    # Drop the one-time code from the URL so a page refresh cannot replay it.
    st.query_params.clear()

# ── SCA (global blocking — only active right after a fresh Monzo login) ───────

_sca_token = st.session_state.get("monzo_token")
if st.session_state.get("monzo_needs_sca") and _sca_token:
    st.info("Monzo sent a notification to your phone. Approve it there, then click continue.")
    # get_accounts() must stay inside this handler: it is the SCA check itself.
    if st.button("I've approved it — continue"):
        try:
            get_accounts(_sca_token)
            del st.session_state["monzo_needs_sca"]
            st.rerun()
        except MonzoSCARequired:
            st.warning("Not approved yet — check your Monzo app and click again when done.")
        except requests.RequestException:
            st.error("Something went wrong checking your Monzo connection. Please try again.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# MONZO SECTION
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("Monzo")

monzo_rows: list[dict] = []

if not cfg.monzo_enabled:
    st.caption(
        "Not configured. Add `monzo_client_id` and `monzo_client_secret` to `config.json` "
        "to import straight from Monzo (see the README)."
    )
else:
    # Ensure we have a token (silent load/refresh)
    if "monzo_token" not in st.session_state:
        token = _load_or_refresh_token()
        if token:
            st.session_state["monzo_token"] = token

if cfg.monzo_enabled and "monzo_token" not in st.session_state:
    # Generate state once per session and persist it so the callback can verify it.
    if "monzo_oauth_state" not in st.session_state:
        state = secrets.token_urlsafe(16)
        save_oauth_state(state, cfg.monzo_token_path)
        st.session_state["monzo_oauth_state"] = state
    st.warning("Not connected to Monzo.")
    st.link_button(
        "Connect to Monzo",
        get_auth_url(cfg.monzo_client_id, st.session_state["monzo_oauth_state"], cfg.redirect_uri),
    )
    st.caption("After connecting, check your Monzo app and tap the approval notification before continuing.")
elif cfg.monzo_enabled:
    access_token: str = st.session_state["monzo_token"]

    try:
        accounts = get_accounts(access_token)
    except MonzoSCARequired:
        st.session_state["monzo_needs_sca"] = True
        st.rerun()
    except requests.RequestException as exc:
        st.warning(f"Monzo connection error: {exc}. Clearing token — please reconnect.")
        _clear_monzo_token()
        st.rerun()

    retail_accounts = [a for a in accounts if a["type"] == "uk_retail"]
    if not retail_accounts:
        st.error("No personal Monzo account found.")
    else:
        account_id: str = retail_accounts[0]["id"]

        now = datetime.now()
        month_names = list(MONTH_TABS.values())
        default_month_index = (now.month - 2) % 12
        default_year = now.year if now.month > 1 else now.year - 1

        col1, col2 = st.columns(2)
        with col1:
            selected_year: int = st.selectbox(
                "Year", [now.year - 1, now.year], index=0 if default_year == now.year - 1 else 1
            )
        with col2:
            selected_month_name: str = st.selectbox("Month", month_names, index=default_month_index)
        selected_month_num: int = month_names.index(selected_month_name) + 1

        if st.button("Fetch transactions"):
            _, last_day = monthrange(selected_year, selected_month_num)
            since = datetime(selected_year, selected_month_num, 1, tzinfo=timezone.utc)
            before = datetime(selected_year, selected_month_num, last_day, 23, 59, 59, tzinfo=timezone.utc)
            try:
                st.session_state["monzo_transactions"] = get_transactions(
                    access_token, account_id, since, before
                )
            except MonzoSCARequired:
                _clear_monzo_token()
                st.rerun()
            except requests.RequestException as exc:
                st.error(
                    f"Failed to fetch transactions: {exc}. Monzo only serves transactions older "
                    "than 90 days for five minutes after you authenticate — reconnect and retry."
                )

        if "monzo_transactions" in st.session_state:
            raw_transactions = st.session_state["monzo_transactions"]
            df = parse_monzo_transactions(raw_transactions, cfg.monzo_category_map)
            bills_df = parse_bill_transactions(raw_transactions)
            income_df = parse_income_transactions(raw_transactions)

            if df.empty and bills_df.empty and income_df.empty:
                st.info("No transactions found after filtering.")
            else:
                filtered_out = [
                    {
                        "Date": datetime.fromisoformat(t["created"].replace("Z", "+00:00")).strftime("%d/%m/%Y"),
                        "Amount": round(-t["amount"] / 100, 2),
                        "Category": t.get("category", ""),
                        "Description": (t.get("merchant") or {}).get("name") or t.get("description", ""),
                        "Reason": (
                            f"declined ({t['decline_reason'].lower().replace('_', ' ')})"
                            if is_declined(t)
                            else "pot transfer" if is_pot_transfer(t) else "transfer"
                        ),
                    }
                    for t in raw_transactions
                    if is_filtered_out(t)
                ]

                ui.render_summary(
                    {"transactions": len(df), "bills": len(bills_df), "income": len(income_df)},
                    suffix=f"{len(raw_transactions)} fetched, {len(filtered_out)} filtered out",
                )
                ui.render_filtered_out(filtered_out)

                if not df.empty:
                    monzo_rows += ui.render_expense_editor(
                        df,
                        categories=cfg.categories,
                        key_prefix="monzo",
                        source=MONZO,
                        caption="Rows with a blank category are exported as-is — fill them in here if you want them categorised.",
                    )

                if not bills_df.empty:
                    monzo_rows += ui.render_bill_editor(bills_df, key_prefix="monzo", source=MONZO)

                if not income_df.empty:
                    monzo_rows += ui.render_income_editor(income_df, key_prefix="monzo", source=MONZO)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# REVOLUT SECTION
# ══════════════════════════════════════════════════════════════════════════════

st.subheader("Revolut")
st.caption("Export a monthly CSV statement from the Revolut app (Accounts → Statement → CSV) and upload it here.")

revolut_rows: list[dict] = []
rate_failures = 0

uploaded = st.file_uploader("Revolut CSV statement", type="csv", label_visibility="collapsed")

if uploaded is not None:
    file_id = (uploaded.name, uploaded.size)
    if st.session_state.get("revolut_file_id") != file_id:
        with st.spinner("Parsing statement and fetching exchange rates..."):
            try:
                rev_expenses_df, rev_income_df, rev_filtered_out = parse_revolut_csv(uploaded)
            except (KeyError, ValueError) as exc:
                st.error(f"Could not read this CSV — is it a Revolut statement export? ({exc})")
                st.stop()
        st.session_state["revolut_file_id"] = file_id
        st.session_state["revolut_expenses_df"] = rev_expenses_df
        st.session_state["revolut_income_df"] = rev_income_df
        st.session_state["revolut_filtered_out"] = rev_filtered_out

    rev_expenses_df = st.session_state["revolut_expenses_df"]
    rev_income_df = st.session_state["revolut_income_df"]
    rev_filtered_out = st.session_state["revolut_filtered_out"]

    if rev_expenses_df.empty and rev_income_df.empty:
        st.info("No importable transactions found after filtering.")
    else:
        ui.render_summary(
            {"transactions": len(rev_expenses_df), "income": len(rev_income_df)},
            suffix=f"{len(rev_filtered_out)} filtered out",
        )
        ui.render_filtered_out(rev_filtered_out)

        rate_failures = sum(
            int(frame["Rate Failed"].sum())
            for frame in (rev_expenses_df, rev_income_df)
            if not frame.empty
        )

        if not rev_expenses_df.empty:
            revolut_rows += ui.render_expense_editor(
                rev_expenses_df,
                categories=cfg.categories,
                key_prefix="revolut",
                source=REVOLUT,
                caption=(
                    "Non-GBP amounts converted using historical ECB rates. "
                    "Original amounts are shown in the 'Original' column."
                ),
            )

        if not rev_income_df.empty:
            revolut_rows += ui.render_income_editor(rev_income_df, key_prefix="revolut", source=REVOLUT)

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════

all_rows = sort_transactions(monzo_rows + revolut_rows)

if not all_rows:
    st.stop()

if rate_failures:
    st.error(
        f"{rate_failures} Revolut row(s) could not be converted to GBP and still show a foreign "
        "amount (marked ⚠️ in the 'Original' column). Correct the Amount cells above before downloading."
    )

st.subheader("Download")
ui.render_download_buttons(all_rows)
