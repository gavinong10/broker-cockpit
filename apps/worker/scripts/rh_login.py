"""Interactive Robinhood login -> <repo>/secrets/rh-session.pickle.

Run once on a trusted machine (handles MFA):

    cd apps/worker && uv run python scripts/rh_login.py

Prompts for username/password/MFA interactively — credentials are NEVER read
from env vars or argv and are never persisted; only the session token pickle
is written. For the VPS, scp the pickle to /root/broker-cockpit/secrets/.
Re-run this script whenever the session expires (the dashboard/Discord will
nag). Note: if a still-valid pickle exists, robin_stocks reuses it and the
printed lifetime just echoes the request — delete the pickle first to force a
fresh grant.
"""
import getpass
import shutil
import sys
from pathlib import Path

import robin_stocks.robinhood as rh

SECRETS_DIR = Path(__file__).resolve().parents[3] / "secrets"
SESSION_FILE = SECRETS_DIR / "rh-session.pickle"
# robin_stocks.login() treats pickle_path as a *directory* and always names the
# file "robinhood<pickle_name>.pickle" inside it; we rename it afterwards.
ROBIN_STOCKS_PICKLE = SECRETS_DIR / "robinhood.pickle"


def main() -> int:
    SECRETS_DIR.mkdir(exist_ok=True)
    username = input("Robinhood username (email): ").strip()
    password = getpass.getpass("Robinhood password: ")
    mfa_code = getpass.getpass("MFA code (blank if using app approval): ").strip() or None

    result = rh.login(
        username=username,
        password=password,
        mfa_code=mfa_code,
        store_session=True,
        pickle_path=str(SECRETS_DIR),
        # Request a long-lived token; RH clamps to its server-side max. The
        # default (86400 = 24h) would force daily re-logins on the headless
        # worker, which has no refresh flow by design.
        expiresIn=86400 * 365,
    )
    if not result or "access_token" not in result:
        print("Login failed — no session stored.", file=sys.stderr)
        return 1
    granted = result.get("expires_in")
    if granted:
        print(f"Token lifetime granted by Robinhood: {granted} seconds (~{granted / 86400:.1f} days)")

    if ROBIN_STOCKS_PICKLE.exists():
        shutil.move(str(ROBIN_STOCKS_PICKLE), str(SESSION_FILE))
    if not SESSION_FILE.exists():
        print(f"Login succeeded but no pickle found at {ROBIN_STOCKS_PICKLE}.", file=sys.stderr)
        return 1

    profile = rh.load_account_profile()
    print(f"Login OK — account number: {profile['account_number']}")
    print(f"Session pickle written to {SESSION_FILE}")
    print("If deploying: scp it to /root/broker-cockpit/secrets/ on the VPS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
