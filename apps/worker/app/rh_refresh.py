"""Owner-triggered Robinhood session refresh (fresh credential login).

SECURITY CONTRACT — type-password-each-time:
The password arrives over the internal docker network, lives in memory for
exactly one robin_stocks ``login()`` call, and is NEVER persisted, logged,
echoed into exception messages, or written to audit payloads. Every error
path below scrubs the password defensively before the message can escape.

robin_stocks ``login()`` quirks this module works around (authentication.py):
- A still-valid pickle short-circuits ``login()`` to the cached-session path;
  a refresh must mint a FRESH token, so both pickle files are deleted first.
- ``pickle_path`` is treated as a DIRECTORY and the file is hard-named
  ``robinhood.pickle``; we rename it to ``rh-session.pickle`` on success.
- "prompt" (device-push) challenges poll internally and are headless-safe,
  but "sms"/"email" challenges call ``builtins.input()`` — which would hang
  the worker forever — so input is patched for the duration of the call.
- ``login()`` wraps its challenge flow in a broad ``except Exception`` that
  swallows anything raised from our patched input and returns ``None``; the
  NeedsCode signal is therefore also recorded in a closure flag and re-raised
  after ``login()`` returns.
"""
import builtins
import os
import threading
from pathlib import Path
from unittest.mock import patch

from app import robinhood
from app.config import settings

# Request a year; RH clamps server-side (~407891s). We report what was granted.
_REQUESTED_LIFETIME_S = 86400 * 365


class NeedsCode(Exception):
    """RH issued an sms/email challenge and no (unconsumed) code was supplied."""

    def __init__(self, channel: str):
        super().__init__(f"verification code required via {channel}")
        self.channel = channel


class Busy(Exception):
    """A refresh is already in flight (single-flight guard)."""


class RHRefreshError(Exception):
    """Refresh failed. Message is guaranteed scrubbed of the password."""


# Single-flight: one refresh at a time, process-wide. Also serializes the
# builtins.input patch below, which mutates global state.
_refresh_lock = threading.Lock()


def _channel_from_prompt(prompt: object) -> str:
    p = str(prompt).lower()
    if "sms" in p:
        return "sms"
    if "email" in p:
        return "email"
    return "code"


def _scrub(message: str, password: str) -> str:
    """Defensively remove the password from any outbound error text."""
    if password and password in message:
        message = message.replace(password, "[redacted]")
    return message


def refresh_session(username: str, password: str, code: str | None = None) -> dict:
    """Mint a fresh RH session pickle via a credential login.

    Returns {"status": "ok", "expires_in": <granted seconds or None>}.
    Raises NeedsCode / Busy / RHRefreshError.
    """
    if not _refresh_lock.acquire(blocking=False):
        raise Busy("a Robinhood session refresh is already in progress")
    try:
        return _do_refresh(username, password, code)
    finally:
        _refresh_lock.release()


def _do_refresh(username: str, password: str, code: str | None) -> dict:
    import robin_stocks.robinhood as rh

    session_file = Path(settings.rh_session_file)
    secrets_dir = session_file.parent
    interim = secrets_dir / "robinhood.pickle"  # hard-coded name inside login()

    # A still-valid pickle makes login() reuse the cached token instead of
    # minting a fresh one — delete both the final and interim files first.
    for stale in (session_file, interim):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass

    state: dict = {"consumed": False, "needs": None}

    def _patched_input(prompt: object = "") -> str:
        # Headless stand-in for the sms/email interactive fallback: hand over
        # the owner-supplied code exactly once, otherwise abort with NeedsCode.
        channel = _channel_from_prompt(prompt)
        if code and not state["consumed"]:
            state["consumed"] = True
            return code
        exc = NeedsCode(channel)
        state["needs"] = exc  # login() may swallow this raise — keep a copy
        raise exc

    try:
        # Patch scoped to this call only (context manager). Global-ish, but the
        # module lock guarantees a single concurrent refresh and nothing else
        # in the worker ever calls input().
        with patch.object(builtins, "input", _patched_input):
            result = rh.login(
                username=username,
                password=password,
                mfa_code=code or None,
                store_session=True,
                pickle_path=str(secrets_dir),  # login() treats this as a dir
                expiresIn=_REQUESTED_LIFETIME_S,
            )
    except NeedsCode:
        raise
    except Exception as exc:
        # NEVER let the password leak through robin_stocks error text.
        raise RHRefreshError(_scrub(str(exc), password)) from None

    if state["needs"] is not None:
        raise state["needs"]
    if not result or "access_token" not in result:
        raise RHRefreshError("Robinhood login failed (no access token granted)")

    if interim.exists():
        interim.replace(session_file)
    if not session_file.exists():
        raise RHRefreshError(f"login succeeded but no session pickle at {interim}")
    os.chmod(session_file, 0o600)

    # Validate the fresh session with an authenticated GET before declaring ok.
    try:
        robinhood.rh_session()
    except robinhood.RHAuthError as exc:
        raise RHRefreshError(_scrub(str(exc), password)) from None

    return {"status": "ok", "expires_in": result.get("expires_in")}
