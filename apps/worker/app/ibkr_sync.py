"""IBKR position sync: normalize ib_async portfolio items into the shared
instruments/positions tables under broker='ibkr'.

Mirrors the Robinhood module's semantics exactly: full-mirror per account
(vanished positions deleted), idempotent, single transaction per account.
Scoped strictly to the ibkr broker_account rows — a sync here can never
touch Robinhood-owned positions.

ib_async facts this module relies on (verified against .venv source):
- ``IB.portfolio(account='')`` is a plain synchronous accessor over the
  wrapper's cached state (ib.py:528) — safe to call inline.
- ``IB.accountSummary()`` is *blocking on first run* (wraps
  ``self._run(accountSummaryAsync())``, ib.py:516) — inside our running
  event loop we must ``await ib.accountSummaryAsync()`` instead.
- TWS ``averageCost`` is per share for STK but PER CONTRACT for OPT (it
  already includes the multiplier); ib_async passes it through unchanged
  (decoder.py:249), so OPT avg cost is divided by the multiplier here.
- ``Contract.multiplier`` is a string; ``lastTradeDateOrContractMonth``
  is 'YYYYMMDD' for options; ``right`` may be 'C'/'CALL'/'P'/'PUT'.
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.robinhood import PositionRow, occ_symbol

log = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 900  # 15 min while the gateway is connected

CASH_TAG = "TotalCashValue"


@dataclass(frozen=True)
class IbkrPositionRow(PositionRow):
    """PositionRow plus the IBKR contract id (instruments dedup key)."""
    con_id: int | None = None


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def normalize_items(items) -> list[IbkrPositionRow]:
    """Pure: ib_async PortfolioItems -> PositionRows. All Decimals, all USD."""
    rows: list[IbkrPositionRow] = []
    for item in items:
        qty = Decimal(str(item.position))
        if qty == 0:
            continue
        c = item.contract
        con_id = c.conId or None
        if c.secType == "OPT":
            expiry = datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date()
            strike = Decimal(str(c.strike))
            right = c.right[0]  # 'C'/'CALL' -> 'C', 'P'/'PUT' -> 'P'
            multiplier = int(c.multiplier)
            rows.append(IbkrPositionRow(
                symbol=occ_symbol(c.symbol, expiry, right, strike),
                sec_type="OPT",
                qty=qty,
                # TWS averageCost for options includes the multiplier
                # (per contract) — store per-share like the RH module does.
                avg_cost_usd=Decimal(str(item.averageCost)) / multiplier,
                last_price_usd=_dec(item.marketPrice),
                prev_close_usd=None,  # not available from portfolio updates
                expiry=expiry,
                strike=strike,
                right=right,
                multiplier=multiplier,
                con_id=con_id,
            ))
        else:  # STK: averageCost is already per share
            rows.append(IbkrPositionRow(
                symbol=c.symbol,
                sec_type=c.secType,
                qty=qty,
                avg_cost_usd=Decimal(str(item.averageCost)),
                last_price_usd=_dec(item.marketPrice),
                prev_close_usd=None,
                con_id=con_id,
            ))
    return rows


def extract_cash(summary_rows) -> Decimal:
    """USD TotalCashValue from an accountSummary row list."""
    for row in summary_rows:
        if row.tag == CASH_TAG and row.currency == "USD":
            return Decimal(row.value)
    raise ValueError(f"no USD {CASH_TAG} row in account summary")


def upsert_ibkr(engine: Engine, account_id_external: str,
                rows: list[IbkrPositionRow], cash: Decimal) -> None:
    """Single-transaction full-mirror upsert for one ibkr account.

    Instrument dedup: match on con_id first when present, else fall back to
    (symbol, sec_type, expiry, strike, right); backfill con_id onto rows the
    RH sync created without one.
    """
    with engine.begin() as conn:
        account_id = conn.execute(text(
            "INSERT INTO broker_accounts (broker, external_id, base_currency, cash_usd, last_synced_at) "
            "VALUES ('ibkr', :ext, 'USD', :cash, now()) "
            "ON CONFLICT (broker, external_id) "
            "DO UPDATE SET cash_usd = EXCLUDED.cash_usd, last_synced_at = now() "
            "RETURNING id"),
            {"ext": account_id_external, "cash": cash}).scalar_one()

        kept_instrument_ids: list[int] = []
        for row in rows:
            params = {
                "sym": row.symbol, "st": row.sec_type, "exp": row.expiry,
                "strike": row.strike, "right": row.right, "mult": row.multiplier,
                "con_id": row.con_id,
            }
            instrument_id = None
            if row.con_id is not None:
                instrument_id = conn.execute(text(
                    "SELECT id FROM instruments WHERE con_id = :con_id"),
                    params).scalar()
            if instrument_id is None:
                instrument_id = conn.execute(text(
                    "SELECT id FROM instruments WHERE symbol = :sym AND sec_type = :st "
                    "AND expiry IS NOT DISTINCT FROM CAST(:exp AS date) "
                    "AND strike IS NOT DISTINCT FROM CAST(:strike AS numeric) "
                    "AND \"right\" IS NOT DISTINCT FROM CAST(:right AS varchar)"),
                    params).scalar()
                if instrument_id is not None and row.con_id is not None:
                    conn.execute(text(
                        "UPDATE instruments SET con_id = :con_id "
                        "WHERE id = :id AND con_id IS NULL"),
                        {"con_id": row.con_id, "id": instrument_id})
            if instrument_id is None:
                instrument_id = conn.execute(text(
                    "INSERT INTO instruments (symbol, sec_type, currency, con_id, expiry, strike, \"right\", multiplier) "
                    "VALUES (:sym, :st, 'USD', :con_id, :exp, :strike, :right, :mult) RETURNING id"),
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


async def run(engine: Engine, ib) -> None:
    """One full sync pass: portfolio + account summary -> per-account upserts.

    ``ib.portfolio()`` is a sync cache accessor; account summary must be
    awaited (see module docstring). Audits sync.ibkr.ok/error; re-raises.
    """
    try:
        items = ib.portfolio()
        summary = await ib.accountSummaryAsync()

        by_account: dict[str, list] = {}
        for item in items:
            by_account.setdefault(item.account, []).append(item)
        # accounts with cash but zero positions still get mirrored (and any
        # stale position rows full-mirror-deleted)
        cash_accounts = {r.account for r in summary
                         if r.tag == CASH_TAG and r.currency == "USD"}
        accounts = sorted(set(by_account) | cash_accounts)

        equity_positions = option_positions = 0
        for account in accounts:
            rows = normalize_items(by_account.get(account, []))
            cash = extract_cash([r for r in summary if r.account == account])
            upsert_ibkr(engine, account, rows, cash)
            equity_positions += sum(1 for r in rows if r.sec_type == "STK")
            option_positions += sum(1 for r in rows if r.sec_type == "OPT")
    except Exception as exc:
        _audit(engine, "sync.ibkr.error",
               {"error": exc.__class__.__name__, "detail": str(exc)})
        raise
    _audit(engine, "sync.ibkr.ok", {
        "accounts": accounts,
        "equity_positions": equity_positions,
        "option_positions": option_positions,
    })
    log.info("ibkr sync ok: accounts=%s equities=%d options=%d",
             accounts, equity_positions, option_positions)


# --- connect-triggered 15-min loop ------------------------------------------

_sync_task: asyncio.Task | None = None  # module-level guard: no double-start


def start_sync_task(engine: Engine, gateway) -> None:
    """Start the 15-min sync loop once per connected session.

    Called from the gateway's on-connect hook; a still-running task from a
    previous connect (brief reconnect) is left alone.
    """
    global _sync_task
    if _sync_task is not None and not _sync_task.done():
        return
    _sync_task = asyncio.create_task(_sync_while_connected(engine, gateway))


async def _sync_while_connected(engine: Engine, gateway) -> None:
    """Sync immediately on connect, then every 15 min while connected.
    Exits when the gateway drops; the next connect starts a fresh task."""
    while gateway.connected:
        try:
            await run(engine, gateway.ib)
        except Exception as exc:
            log.warning("ibkr sync failed: %s: %s", exc.__class__.__name__, exc)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
