"""Monzo OAuth and API client."""

import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import requests

AUTH_URL = "https://auth.monzo.com/"
TOKEN_URL = "https://api.monzo.com/oauth2/token"
API_BASE = "https://api.monzo.com"
PAGE_SIZE = 100
REQUEST_TIMEOUT = 20
_STATE_FILENAME = "monzo-oauth-state.txt"


class MonzoSCARequired(Exception):
    """Raised when Monzo requires the user to approve the session in their app."""


def _ensure_parent(path: str | Path) -> Path:
    """Return *path*, creating its parent directory if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_auth_url(client_id: str, state: str, redirect_uri: str) -> str:
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    })
    return f"{AUTH_URL}?{params}"


def _state_path(token_path: str) -> Path:
    return Path(token_path).parent / _STATE_FILENAME


def save_oauth_state(state: str, token_path: str) -> None:
    _ensure_parent(_state_path(token_path)).write_text(state)


def load_oauth_state(token_path: str) -> str | None:
    p = _state_path(token_path)
    return p.read_text().strip() if p.exists() else None


def clear_oauth_state(token_path: str) -> None:
    _state_path(token_path).unlink(missing_ok=True)


def save_monzo_token(token_data: dict, path: str) -> None:
    with _ensure_parent(path).open("w") as fh:
        json.dump({
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "expires_at": time.time() + token_data.get("expires_in", 0),
        }, fh)


def load_monzo_token(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        with p.open() as fh:
            token = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    if not {"access_token", "refresh_token", "expires_at"} <= token.keys():
        return None
    return token


def refresh_monzo_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _error_payload(resp: requests.Response) -> dict:
    try:
        payload = resp.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _check_response(resp: requests.Response) -> None:
    if resp.status_code == 403:
        payload = _error_payload(resp)
        if payload.get("code", "") == "forbidden.verification_required":
            raise MonzoSCARequired
        raise requests.HTTPError(
            f"Monzo API error ({payload.get('code', resp.status_code)}): "
            f"{payload.get('message', resp.text)}",
            response=resp,
        )
    resp.raise_for_status()


def get_accounts(access_token: str) -> list[dict]:
    resp = requests.get(
        f"{API_BASE}/accounts",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=REQUEST_TIMEOUT,
    )
    _check_response(resp)
    return resp.json()["accounts"]


def get_transactions(
    access_token: str,
    account_id: str,
    since: datetime,
    before: datetime,
) -> list[dict]:
    """All transactions between *since* and *before*, following pagination.

    Note: Monzo only serves transactions older than 90 days for five minutes
    after authentication — see the README.
    """
    all_transactions: list[dict] = []
    cursor: str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    while True:
        resp = requests.get(
            f"{API_BASE}/transactions",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "account_id": account_id,
                "since": cursor,
                "before": before.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expand[]": "merchant",
                "limit": PAGE_SIZE,
            },
            timeout=REQUEST_TIMEOUT,
        )
        _check_response(resp)
        page = resp.json()["transactions"]
        all_transactions.extend(page)
        if len(page) < PAGE_SIZE:
            break
        cursor = page[-1]["id"]

    return all_transactions
