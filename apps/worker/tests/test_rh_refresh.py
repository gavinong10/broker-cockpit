"""Tests for the owner-triggered RH session refresh (rh_refresh + endpoint).

robin_stocks is mocked entirely — zero live Robinhood calls. The password used
in these tests is a sentinel string so we can assert it never leaks into error
messages.
"""
import builtins
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import rh_refresh
from app.config import settings
from app.main import app
from app.rh_refresh import Busy, NeedsCode, RHRefreshError, refresh_session
from app.robinhood import SyncResult

PASSWORD = "sentinel-hunter2-password"
GRANT = {"access_token": "tok", "token_type": "Bearer", "expires_in": 407891,
         "refresh_token": "r", "scope": "internal"}


@pytest.fixture
def session_dir(tmp_path, monkeypatch):
    """Point settings.rh_session_file into a tmp dir; return the dir."""
    monkeypatch.setattr(settings, "rh_session_file", str(tmp_path / "rh-session.pickle"))
    return tmp_path


def _login_ok(pickle_dir):
    """Fake login: writes the interim robinhood.pickle the way robin_stocks does."""
    def fake_login(**kwargs):
        (pickle_dir / "robinhood.pickle").write_bytes(b"fake-session")
        return dict(GRANT)
    return fake_login


def test_success_path(session_dir):
    with patch("robin_stocks.robinhood.login", side_effect=_login_ok(session_dir)) as login, \
         patch("app.robinhood.rh_session") as rh_session:
        result = refresh_session("user@example.com", PASSWORD, None)

    login.assert_called_once()
    kwargs = login.call_args.kwargs
    assert kwargs["username"] == "user@example.com"
    assert kwargs["password"] == PASSWORD
    assert kwargs["mfa_code"] is None
    assert kwargs["store_session"] is True
    assert kwargs["pickle_path"] == str(session_dir)          # the DIRECTORY
    assert kwargs["expiresIn"] == 86400 * 365
    rh_session.assert_called_once()

    final = session_dir / "rh-session.pickle"
    assert final.exists()
    assert not (session_dir / "robinhood.pickle").exists()    # renamed, not copied
    assert (os.stat(final).st_mode & 0o777) == 0o600
    assert result == {"status": "ok", "expires_in": 407891}


def test_prompt_flow_never_calls_input(session_dir):
    """Device-push ("prompt") challenges poll internally — input is never hit."""
    calls = []

    def fake_login(**kwargs):
        # record whether the patched input would have been consulted — it isn't
        calls.append("login")
        (session_dir / "robinhood.pickle").write_bytes(b"fake")
        return dict(GRANT)

    original_input = builtins.input
    with patch("robin_stocks.robinhood.login", side_effect=fake_login), \
         patch("app.robinhood.rh_session"):
        result = refresh_session("u", PASSWORD, None)
    assert result["status"] == "ok"
    assert builtins.input is original_input                    # patch was scoped


def test_sms_challenge_without_code_raises_needs_code(session_dir):
    def fake_login(**kwargs):
        # mimics robin_stocks _validate_sherrif_id's interactive fallback
        input("Enter the sms verification code sent to your device: ")
        raise AssertionError("input should have raised NeedsCode")

    with patch("robin_stocks.robinhood.login", side_effect=fake_login), \
         patch("app.robinhood.rh_session"):
        with pytest.raises(NeedsCode) as exc:
            refresh_session("u", PASSWORD, None)
    assert exc.value.channel == "sms"


def test_email_challenge_channel(session_dir):
    def fake_login(**kwargs):
        input("Enter the email verification code sent to your device: ")

    with patch("robin_stocks.robinhood.login", side_effect=fake_login):
        with pytest.raises(NeedsCode) as exc:
            refresh_session("u", PASSWORD, None)
    assert exc.value.channel == "email"


def test_needs_code_recovered_when_login_swallows_it(session_dir):
    """Real login() wraps the challenge flow in `except Exception` and returns
    None — the NeedsCode raised inside input() must still surface."""
    def fake_login(**kwargs):
        try:
            input("Enter the sms verification code sent to your device: ")
        except Exception:
            pass  # exactly what robin_stocks does
        return None

    with patch("robin_stocks.robinhood.login", side_effect=fake_login):
        with pytest.raises(NeedsCode) as exc:
            refresh_session("u", PASSWORD, None)
    assert exc.value.channel == "sms"


def test_sms_challenge_with_code_succeeds(session_dir):
    seen = []

    def fake_login(**kwargs):
        seen.append(input("Enter the sms verification code sent to your device: "))
        (session_dir / "robinhood.pickle").write_bytes(b"fake")
        return dict(GRANT)

    with patch("robin_stocks.robinhood.login", side_effect=fake_login), \
         patch("app.robinhood.rh_session"):
        result = refresh_session("u", PASSWORD, "123456")
    assert seen == ["123456"]
    assert result["status"] == "ok"


def test_stale_pickles_deleted_before_login(session_dir):
    (session_dir / "rh-session.pickle").write_bytes(b"stale")
    (session_dir / "robinhood.pickle").write_bytes(b"stale-interim")

    def fake_login(**kwargs):
        # a still-valid pickle would short-circuit login() to the cached path;
        # refresh must mint a fresh token, so both must be gone by now
        assert not (session_dir / "rh-session.pickle").exists()
        assert not (session_dir / "robinhood.pickle").exists()
        (session_dir / "robinhood.pickle").write_bytes(b"fresh")
        return dict(GRANT)

    with patch("robin_stocks.robinhood.login", side_effect=fake_login), \
         patch("app.robinhood.rh_session"):
        assert refresh_session("u", PASSWORD, None)["status"] == "ok"


def test_password_never_in_error_text(session_dir):
    def fake_login(**kwargs):
        raise Exception(f"401 bad credentials for password={PASSWORD} try again")

    with patch("robin_stocks.robinhood.login", side_effect=fake_login):
        with pytest.raises(RHRefreshError) as exc:
            refresh_session("u", PASSWORD, None)
    assert PASSWORD not in str(exc.value)


def test_failed_login_none_result(session_dir):
    with patch("robin_stocks.robinhood.login", return_value=None):
        with pytest.raises(RHRefreshError):
            refresh_session("u", PASSWORD, None)


def test_busy_when_lock_held(session_dir):
    assert rh_refresh._refresh_lock.acquire(blocking=False)
    try:
        with pytest.raises(Busy):
            refresh_session("u", PASSWORD, None)
    finally:
        rh_refresh._refresh_lock.release()


# ---------------------------------------------------------------- endpoint ---

BODY = {"username": "u@example.com", "password": PASSWORD, "actor": "owner@example.com"}
HDRS = {"X-Internal-Token": "dev-token"}


def test_endpoint_401_without_token():
    c = TestClient(app)
    assert c.post("/internal/rh/refresh", json=BODY).status_code == 401


def test_endpoint_422_missing_fields():
    c = TestClient(app)
    r = c.post("/internal/rh/refresh", json={"username": "u"}, headers=HDRS)
    assert r.status_code == 422


def test_endpoint_needs_code_428():
    c = TestClient(app)
    with patch("app.rh_refresh.refresh_session", side_effect=NeedsCode("sms")):
        r = c.post("/internal/rh/refresh", json=BODY, headers=HDRS)
    assert r.status_code == 428
    assert r.json() == {"status": "needs_code", "channel": "sms"}


def test_endpoint_ok_with_sync():
    from decimal import Decimal
    sync = SyncResult(account_external_id="ACC1", equity_positions=3,
                      option_positions=2, cash_usd=Decimal("100.50"))
    c = TestClient(app)
    with patch("app.rh_refresh.refresh_session",
               return_value={"status": "ok", "expires_in": 407891}), \
         patch("app.main.sync_robinhood", return_value=sync):
        r = c.post("/internal/rh/refresh", json=BODY, headers=HDRS)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["expires_in"] == 407891
    assert body["sync"]["account_external_id"] == "ACC1"
    assert body["sync"]["equity_positions"] == 3
    assert body["sync"]["cash_usd"] == "100.50"


def test_endpoint_ok_sync_failure_does_not_fail_response():
    c = TestClient(app)
    with patch("app.rh_refresh.refresh_session",
               return_value={"status": "ok", "expires_in": 100}), \
         patch("app.main.sync_robinhood", side_effect=RuntimeError("rh hiccup")):
        r = c.post("/internal/rh/refresh", json=BODY, headers=HDRS)
    assert r.status_code == 200
    assert r.json()["sync"] == {"error": "rh hiccup"}


def test_endpoint_failed_502():
    c = TestClient(app)
    with patch("app.rh_refresh.refresh_session", side_effect=RHRefreshError("boom")):
        r = c.post("/internal/rh/refresh", json=BODY, headers=HDRS)
    assert r.status_code == 502
    assert r.json() == {"status": "failed", "detail": "boom"}


def test_endpoint_409_when_busy(session_dir):
    c = TestClient(app)
    assert rh_refresh._refresh_lock.acquire(blocking=False)
    try:
        r = c.post("/internal/rh/refresh", json=BODY, headers=HDRS)
    finally:
        rh_refresh._refresh_lock.release()
    assert r.status_code == 409
    assert r.json() == {"status": "busy"}
