"""Seed harmless demo portfolio data for local UI development.

    docker-compose exec worker uv run python scripts/seed_demo.py

Inserts one demo broker account (robinhood/DEMO-0001) with cash, three
equities (one fractional), one call option, and three daily portfolio
snapshots (so the value chart draws a line). REFUSES to run if any
broker_accounts row already exists — this must never touch a database that
has seen a real sync.
"""
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from app.config import settings

EQUITIES = [
    # symbol, qty, avg_cost, last, prev_close
    ("AAPL", "12", "171.25", "227.50", "225.10"),
    ("NVDA", "3.5", "96.40", "158.90", "161.35"),
    ("SPY", "5", "512.00", "622.40", "619.80"),
]
# AAPL Dec-18-2026 $150 call: OCC symbol {SYM}{YYMMDD}{C/P}{strike*1000:08d}
OPTION = ("AAPL261218C00150000", "2026-12-18", "150.0000", "C", 100,
          "1", "45.10", "82.35", "80.90")
CASH_USD = "2500.00"
# (days ago, total value) — three fake days ending today, chart draws a line.
SNAPSHOTS = [(2, "16810.40"), (1, "16954.93"), (0, "17133.15")]


def main() -> int:
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT count(*) FROM broker_accounts")).scalar_one()
        if existing:
            print(
                f"REFUSING to seed: {existing} broker_accounts row(s) already "
                "exist — this script is for empty local databases only.",
                file=sys.stderr,
            )
            return 1

        account_id = conn.execute(text(
            "INSERT INTO broker_accounts "
            "(broker, external_id, base_currency, cash_usd, last_synced_at) "
            "VALUES ('robinhood', 'DEMO-0001', 'USD', :cash, :now) RETURNING id"),
            {"cash": CASH_USD, "now": datetime.now(timezone.utc)}).scalar_one()

        def insert_position(instrument_id: int, qty, avg, last, prev) -> None:
            conn.execute(text(
                "INSERT INTO positions (broker_account_id, instrument_id, qty, "
                "avg_cost_usd, last_price_usd, prev_close_usd) "
                "VALUES (:a, :i, :q, :c, :l, :p)"),
                {"a": account_id, "i": instrument_id,
                 "q": qty, "c": avg, "l": last, "p": prev})

        for symbol, qty, avg, last, prev in EQUITIES:
            iid = conn.execute(text(
                "INSERT INTO instruments (symbol, sec_type, currency, multiplier) "
                "VALUES (:s, 'STK', 'USD', 1) RETURNING id"),
                {"s": symbol}).scalar_one()
            insert_position(iid, qty, avg, last, prev)

        occ, expiry, strike, right, mult, qty, avg, last, prev = OPTION
        iid = conn.execute(text(
            "INSERT INTO instruments (symbol, sec_type, currency, expiry, strike, "
            "\"right\", multiplier) "
            "VALUES (:s, 'OPT', 'USD', :e, :k, :r, :m) RETURNING id"),
            {"s": occ, "e": expiry, "k": strike, "r": right, "m": mult}).scalar_one()
        insert_position(iid, qty, avg, last, prev)

        for days_ago, total in SNAPSHOTS:
            conn.execute(text(
                "INSERT INTO snapshots (taken_on, total_value_usd, cash_usd, per_account) "
                "VALUES (:d, :t, :c, '{}'::jsonb) "
                "ON CONFLICT (taken_on) DO NOTHING"),
                {"d": date.today() - timedelta(days=days_ago),
                 "t": total, "c": CASH_USD})

    print("Seeded demo account DEMO-0001: 3 equities, 1 option, "
          f"${CASH_USD} cash, {len(SNAPSHOTS)} snapshots.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
