"""Robinhood read-only mirror: session, fetch, normalize, idempotent upsert.

RH is a read-only mirror — this module must never call any order endpoint.
The broker is the source of truth; DB positions are a cache rebuilt in full
on every sync (full-mirror semantics: vanished positions are deleted).
"""
import json
import pickle
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import settings


class RHAuthError(Exception):
    """The Robinhood session pickle is missing, invalid, or expired."""


@dataclass
class SyncResult:
    account_external_id: str
    equity_positions: int
    option_positions: int
    cash_usd: Decimal


@dataclass(frozen=True)
class AccountInfo:
    external_id: str
    cash_usd: Decimal


@dataclass(frozen=True)
class PositionRow:
    symbol: str                      # ticker for STK, OCC symbol for OPT
    sec_type: str                    # STK | OPT
    qty: Decimal                     # negative for short options
    avg_cost_usd: Decimal | None     # per share/unit (option premium / multiplier)
    last_price_usd: Decimal | None
    prev_close_usd: Decimal | None
    expiry: date | None = None       # OPT only
    strike: Decimal | None = None
    right: str | None = None         # C | P
    multiplier: int | None = None


def rh_session() -> None:
    """Restore the robin_stocks session from the pickle file only.

    Never performs a credential login. robin_stocks 3.4's ``login()`` treats
    ``pickle_path`` as a directory, hard-codes the file name, and falls back to
    interactive ``input()`` prompts when the stored token is rejected — fatal in
    a headless worker — so we restore the session state directly instead and
    validate it with an authenticated GET.
    """
    from robin_stocks.robinhood import helper as rh_helper
    from robin_stocks.robinhood.urls import positions_url

    try:
        with open(settings.rh_session_file, "rb") as f:
            session = pickle.load(f)
        token = f"{session['token_type']} {session['access_token']}"
    except (OSError, pickle.PickleError, KeyError, EOFError) as exc:
        raise RHAuthError(f"cannot load RH session pickle {settings.rh_session_file}: {exc}") from exc

    rh_helper.set_login_state(True)
    rh_helper.update_session("Authorization", token)
    res = rh_helper.request_get(positions_url(), "pagination", {"nonzero": "true"}, jsonify_data=False)
    if res is None or res.status_code != 200:
        rh_helper.set_login_state(False)
        rh_helper.update_session("Authorization", None)
        status = getattr(res, "status_code", "no response")
        raise RHAuthError(f"RH session rejected ({status}) — re-run scripts/rh_login.py and re-deploy the pickle")


def fetch_raw() -> dict:
    """Fetch account profile, stock and option positions plus lookups. Read-only."""
    import robin_stocks.robinhood as rh

    rh_session()
    profile = rh.load_account_profile()
    stock_positions = rh.get_open_stock_positions() or []
    instruments = {p["instrument"]: rh.get_instrument_by_url(p["instrument"]) for p in stock_positions}
    symbols = sorted({inst["symbol"] for inst in instruments.values()})
    quotes = {q["symbol"]: q for q in (rh.get_quotes(symbols) or []) if q} if symbols else {}
    option_positions = rh.get_open_option_positions() or []
    option_instruments = {
        p["option_id"]: rh.get_option_instrument_data_by_id(p["option_id"]) for p in option_positions
    }
    option_marks = {}
    for p in option_positions:
        data = rh.get_option_market_data_by_id(p["option_id"])  # list of dicts (or None)
        option_marks[p["option_id"]] = data[0] if data else None
    return {
        "account_profile": profile,
        "stock_positions": stock_positions,
        "instruments": instruments,
        "quotes": quotes,
        "option_positions": option_positions,
        "option_instruments": option_instruments,
        "option_marks": option_marks,
    }


def occ_symbol(underlying: str, expiry: date, right: str, strike: Decimal) -> str:
    """OCC-style symbol: {SYM}{YYMMDD}{C|P}{strike*1000:08d} (no padding spaces)."""
    return f"{underlying}{expiry:%y%m%d}{right}{int(strike * 1000):08d}"


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def normalize(raw: dict) -> tuple[AccountInfo, list[PositionRow]]:
    """Pure: raw robin_stocks payloads -> (account, position rows). All Decimals, all USD."""
    profile = raw["account_profile"]
    account = AccountInfo(
        external_id=profile["account_number"],
        cash_usd=Decimal(profile["portfolio_cash"]),
    )

    rows: list[PositionRow] = []
    for pos in raw["stock_positions"]:
        qty = Decimal(pos["quantity"])
        if qty == 0:
            continue
        symbol = raw["instruments"][pos["instrument"]]["symbol"]
        quote = raw["quotes"].get(symbol) or {}
        rows.append(PositionRow(
            symbol=symbol,
            sec_type="STK",
            qty=qty,
            avg_cost_usd=_dec(pos["average_buy_price"]),
            last_price_usd=_dec(quote.get("last_trade_price")),
            prev_close_usd=_dec(quote.get("previous_close")),
        ))

    for pos in raw["option_positions"]:
        qty = Decimal(pos["quantity"])
        if qty == 0:
            continue
        if pos["type"] == "short":
            qty = -qty
        inst = raw["option_instruments"][pos["option_id"]]
        mark = raw["option_marks"].get(pos["option_id"]) or {}
        expiry = date.fromisoformat(inst["expiration_date"])
        strike = Decimal(inst["strike_price"])
        right = "C" if inst["type"] == "call" else "P"
        multiplier = int(Decimal(pos.get("trade_value_multiplier") or "100"))
        # RH average_price is per contract (premium * multiplier), signed by direction;
        # store per-share cost, sign carried by qty.
        avg_cost = abs(Decimal(pos["average_price"])) / multiplier
        rows.append(PositionRow(
            symbol=occ_symbol(inst["chain_symbol"], expiry, right, strike),
            sec_type="OPT",
            qty=qty,
            avg_cost_usd=avg_cost,
            last_price_usd=_dec(mark.get("adjusted_mark_price")),
            prev_close_usd=_dec(mark.get("previous_close_price")),
            expiry=expiry,
            strike=strike,
            right=right,
            multiplier=multiplier,
        ))

    return account, rows


def upsert(engine: Engine, account: AccountInfo, rows: list[PositionRow]) -> None:
    """Single-transaction full-mirror upsert: account, instruments, positions; delete vanished."""
    with engine.begin() as conn:
        account_id = conn.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd, last_synced_at) "
            "VALUES ('robinhood', :ext, 'USD', :cash, now()) "
            "ON CONFLICT (broker, external_id) "
            "DO UPDATE SET cash_usd = EXCLUDED.cash_usd, last_synced_at = now() "
            "RETURNING id"),
            {"ext": account.external_id, "cash": account.cash_usd}).scalar_one()

        kept_instrument_ids: list[int] = []
        for row in rows:
            params = {
                "sym": row.symbol, "st": row.sec_type, "exp": row.expiry,
                "strike": row.strike, "right": row.right, "mult": row.multiplier,
            }
            instrument_id = conn.execute(text(
                "SELECT id FROM instruments WHERE symbol = :sym AND sec_type = :st "
                "AND expiry IS NOT DISTINCT FROM CAST(:exp AS date) "
                "AND strike IS NOT DISTINCT FROM CAST(:strike AS numeric) "
                "AND \"right\" IS NOT DISTINCT FROM CAST(:right AS varchar)"),
                params).scalar()
            if instrument_id is None:
                instrument_id = conn.execute(text(
                    "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, \"right\", multiplier) "
                    "VALUES (:sym, :st, 'USD', :exp, :strike, :right, :mult) RETURNING id"),
                    params).scalar_one()
            conn.execute(text(
                "INSERT INTO positions (broker_account_id, instrument_id, qty, avg_cost_usd, "
                "                       last_price_usd, prev_close_usd) "
                "VALUES (:acct, :inst, :qty, :avg, :last, :prev) "
                "ON CONFLICT (broker_account_id, instrument_id) DO UPDATE SET "
                "qty = EXCLUDED.qty, avg_cost_usd = EXCLUDED.avg_cost_usd, "
                "last_price_usd = EXCLUDED.last_price_usd, prev_close_usd = EXCLUDED.prev_close_usd, "
                "updated_at = now()"),
                {"acct": account_id, "inst": instrument_id, "qty": row.qty,
                 "avg": row.avg_cost_usd, "last": row.last_price_usd, "prev": row.prev_close_usd})
            kept_instrument_ids.append(instrument_id)

        if kept_instrument_ids:
            conn.execute(text(
                "DELETE FROM positions WHERE broker_account_id = :acct "
                "AND NOT (instrument_id = ANY(:kept))"),
                {"acct": account_id, "kept": kept_instrument_ids})
        else:
            conn.execute(text("DELETE FROM positions WHERE broker_account_id = :acct"),
                         {"acct": account_id})


def _audit(engine: Engine, category: str, payload: dict) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO audit_log (actor, category, payload) "
                "VALUES ('system', :c, CAST(:p AS jsonb))"),
                {"c": category, "p": json.dumps(payload)})
    except Exception:
        pass  # the audit trail must never mask the sync outcome


def sync_robinhood(engine: Engine) -> SyncResult:
    """Fetch → normalize → upsert; audit sync.robinhood.ok/error; re-raise on failure."""
    try:
        account, rows = normalize(fetch_raw())
        upsert(engine, account, rows)
        result = SyncResult(
            account_external_id=account.external_id,
            equity_positions=sum(1 for r in rows if r.sec_type == "STK"),
            option_positions=sum(1 for r in rows if r.sec_type == "OPT"),
            cash_usd=account.cash_usd,
        )
    except Exception as exc:
        _audit(engine, "sync.robinhood.error",
               {"error": exc.__class__.__name__, "detail": str(exc)})
        raise
    _audit(engine, "sync.robinhood.ok", {
        "account": result.account_external_id,
        "equity_positions": result.equity_positions,
        "option_positions": result.option_positions,
        "cash_usd": str(result.cash_usd),
    })
    return result
