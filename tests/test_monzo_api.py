"""Tests for the Monzo client: OAuth, token storage and pagination."""

import json
import os
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from bank_statement_exporter import monzo_api
from bank_statement_exporter.monzo_api import (
    PAGE_SIZE,
    MonzoSCARequired,
    MonzoTokenError,
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


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


@pytest.fixture
def token_path(tmp_path):
    return str(tmp_path / "config" / "monzo-token.json")


# ── OAuth URL ────────────────────────────────────────────────────────────────

def test_auth_url_carries_the_state_and_redirect():
    url = get_auth_url("client-123", "st4te", "http://localhost:8501/")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["client-123"]
    assert query["state"] == ["st4te"]
    assert query["redirect_uri"] == ["http://localhost:8501/"]
    assert query["response_type"] == ["code"]


def test_auth_url_escapes_the_redirect_rather_than_breaking_the_query():
    url = get_auth_url("c", "s", "http://localhost:8501/?a=b")
    assert "http%3A%2F%2Flocalhost%3A8501%2F%3Fa%3Db" in url


def test_the_client_secret_never_appears_in_the_auth_url():
    assert "secret" not in get_auth_url("client-123", "state", "http://localhost:8501/")


# ── Token storage ────────────────────────────────────────────────────────────

def test_token_round_trips(token_path):
    save_monzo_token({"access_token": "a", "refresh_token": "r", "expires_in": 3600}, token_path)
    saved = load_monzo_token(token_path)
    assert saved["access_token"] == "a" and saved["refresh_token"] == "r"
    assert saved["expires_at"] > 0


def test_saving_a_token_creates_missing_directories(token_path):
    save_monzo_token({"access_token": "a", "refresh_token": "r", "expires_in": 1}, token_path)
    assert load_monzo_token(token_path) is not None


# POSIX modes do not exist on Windows, where os.chmod only toggles read-only;
# those files are protected by the user profile's ACLs instead.
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")


@posix_only
def test_the_token_file_is_not_world_readable(token_path):
    save_monzo_token({"access_token": "a", "refresh_token": "r", "expires_in": 1}, token_path)
    mode = stat.S_IMODE(os.stat(token_path).st_mode)
    assert mode == 0o600, f"refresh token readable by others: {oct(mode)}"


def test_a_missing_token_is_not_an_error(tmp_path):
    assert load_monzo_token(str(tmp_path / "nope.json")) is None


def test_a_corrupt_token_file_is_ignored_rather_than_crashing(tmp_path):
    path = tmp_path / "monzo-token.json"
    path.write_text("{not json")
    assert load_monzo_token(str(path)) is None


def test_a_token_missing_its_refresh_token_is_rejected(tmp_path):
    path = tmp_path / "monzo-token.json"
    path.write_text(json.dumps({"access_token": "a", "expires_at": 1}))
    assert load_monzo_token(str(path)) is None


# ── OAuth state (CSRF) ───────────────────────────────────────────────────────

def test_oauth_state_round_trips_and_clears(token_path):
    save_oauth_state("st4te", token_path)
    assert load_oauth_state(token_path) == "st4te"
    clear_oauth_state(token_path)
    assert load_oauth_state(token_path) is None


def test_clearing_a_state_that_is_not_there_is_harmless(token_path):
    clear_oauth_state(token_path)  # must not raise


@posix_only
def test_the_state_file_is_not_world_readable(token_path):
    save_oauth_state("st4te", token_path)
    path = str(Path(token_path).parent / "monzo-oauth-state.txt")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# ── Error handling ───────────────────────────────────────────────────────────

def test_sca_challenge_is_recognised(monkeypatch):
    monkeypatch.setattr(monzo_api.requests, "get", lambda *a, **k: FakeResponse(
        403, {"code": "forbidden.verification_required"}
    ))
    with pytest.raises(MonzoSCARequired):
        get_accounts("tok")


def test_other_forbidden_errors_are_reported_with_monzos_message(monkeypatch):
    monkeypatch.setattr(monzo_api.requests, "get", lambda *a, **k: FakeResponse(
        403, {"code": "forbidden.insufficient_permissions", "message": "nope"}
    ))
    with pytest.raises(requests.HTTPError, match="nope"):
        get_accounts("tok")


def test_a_403_with_no_json_body_does_not_mask_itself_as_sca(monkeypatch):
    monkeypatch.setattr(monzo_api.requests, "get", lambda *a, **k: FakeResponse(403, None, text="gateway"))
    with pytest.raises(requests.HTTPError):
        get_accounts("tok")


def test_server_errors_propagate(monkeypatch):
    monkeypatch.setattr(monzo_api.requests, "get", lambda *a, **k: FakeResponse(500, {}))
    with pytest.raises(requests.HTTPError):
        get_accounts("tok")


def test_the_access_token_is_sent_as_a_bearer_header(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, **kwargs):
        seen.update(headers or {})
        return FakeResponse(200, {"accounts": []})

    monkeypatch.setattr(monzo_api.requests, "get", fake_get)
    get_accounts("tok")
    assert seen["Authorization"] == "Bearer tok"


def test_requests_have_a_timeout(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, **kwargs):
        seen.update(kwargs)
        return FakeResponse(200, {"accounts": []})

    monkeypatch.setattr(monzo_api.requests, "get", fake_get)
    get_accounts("tok")
    assert seen.get("timeout")  # a hung request must not hang the app forever


# ── Pagination ───────────────────────────────────────────────────────────────

def _page(count, prefix="tx"):
    return [{"id": f"{prefix}_{i}", "amount": -100} for i in range(count)]


def test_a_single_short_page_is_one_request(monkeypatch):
    calls = []

    def fake_get(url, headers=None, params=None, **kwargs):
        calls.append(params)
        return FakeResponse(200, {"transactions": _page(5)})

    monkeypatch.setattr(monzo_api.requests, "get", fake_get)
    from datetime import datetime, timezone
    since = datetime(2025, 3, 1, tzinfo=timezone.utc)
    before = datetime(2025, 3, 31, tzinfo=timezone.utc)

    assert len(get_transactions("tok", "acc", since, before)) == 5
    assert len(calls) == 1
    assert calls[0]["since"] == "2025-03-01T00:00:00Z"
    assert calls[0]["limit"] == PAGE_SIZE


def test_a_full_page_is_followed_by_another_request_from_the_last_id(monkeypatch):
    pages = [_page(PAGE_SIZE, "a"), _page(3, "b")]
    calls = []

    def fake_get(url, headers=None, params=None, **kwargs):
        calls.append(params)
        return FakeResponse(200, {"transactions": pages[len(calls) - 1]})

    monkeypatch.setattr(monzo_api.requests, "get", fake_get)
    from datetime import datetime, timezone
    since = datetime(2025, 3, 1, tzinfo=timezone.utc)
    before = datetime(2025, 3, 31, tzinfo=timezone.utc)

    result = get_transactions("tok", "acc", since, before)
    assert len(result) == PAGE_SIZE + 3
    assert len(calls) == 2
    # The second page continues from the last transaction of the first.
    assert calls[1]["since"] == f"a_{PAGE_SIZE - 1}"


# ── Token exchange and refresh ───────────────────────────────────────────────

def test_authorization_code_is_exchanged_with_the_matching_redirect(monkeypatch):
    seen = {}

    def fake_post(url, data=None, **kwargs):
        seen.update(data or {})
        return FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600})

    monkeypatch.setattr(monzo_api.requests, "post", fake_post)
    token = exchange_code("id", "secret", "the-code", "http://localhost:8501/")
    assert token["access_token"] == "a"
    assert seen["grant_type"] == "authorization_code"
    assert seen["code"] == "the-code"
    assert seen["redirect_uri"] == "http://localhost:8501/"


def test_refresh_sends_the_refresh_grant(monkeypatch):
    seen = {}

    def fake_post(url, data=None, **kwargs):
        seen.update(data or {})
        return FakeResponse(200, {"access_token": "new", "refresh_token": "r2", "expires_in": 3600})

    monkeypatch.setattr(monzo_api.requests, "post", fake_post)
    assert refresh_monzo_token("id", "secret", "old-refresh")["access_token"] == "new"
    assert seen["grant_type"] == "refresh_token"
    assert seen["refresh_token"] == "old-refresh"


def test_a_rejected_refresh_raises(monkeypatch):
    monkeypatch.setattr(monzo_api.requests, "post", lambda *a, **k: FakeResponse(401, {}))
    with pytest.raises(requests.HTTPError):
        refresh_monzo_token("id", "secret", "stale")


def test_a_client_that_returns_no_refresh_token_is_rejected_with_advice(token_path):
    # Monzo only issues refresh tokens to Confidential clients.
    with pytest.raises(MonzoTokenError, match="Confidential"):
        save_monzo_token({"access_token": "a", "expires_in": 3600}, token_path)


def test_a_response_with_no_access_token_is_rejected(token_path):
    with pytest.raises(MonzoTokenError, match="access token"):
        save_monzo_token({"refresh_token": "r"}, token_path)


def test_a_token_with_a_wrong_typed_expiry_is_ignored(tmp_path):
    path = tmp_path / "monzo-token.json"
    path.write_text(json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": "soon"}))
    assert load_monzo_token(str(path)) is None


def test_a_token_that_is_not_an_object_is_ignored(tmp_path):
    path = tmp_path / "monzo-token.json"
    path.write_text("[1, 2, 3]")
    assert load_monzo_token(str(path)) is None
