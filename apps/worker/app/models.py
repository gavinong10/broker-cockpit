import enum
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (BigInteger, Boolean, Computed, Date, DateTime, Enum,
                        ForeignKey, Numeric, SmallInteger, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Role(enum.Enum):
    owner = "owner"
    viewer = "viewer"

class Broker(enum.Enum):
    ibkr = "ibkr"
    robinhood = "robinhood"

class FlowKind(enum.Enum):
    deposit = "deposit"
    withdrawal = "withdrawal"
    acats_in = "acats_in"
    acats_out = "acats_out"

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"))
    mask_amounts: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class BrokerAccount(Base):
    __tablename__ = "broker_accounts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    broker: Mapped[Broker] = mapped_column(Enum(Broker, name="broker"))
    external_id: Mapped[str] = mapped_column(String(64))
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    cash_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2), server_default="0")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("broker", "external_id"),)

class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    sec_type: Mapped[str] = mapped_column(String(8))            # STK | OPT | CASH
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    exchange: Mapped[str | None] = mapped_column(String(16))
    con_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)  # IBKR contract id
    expiry: Mapped[date | None] = mapped_column(Date)            # OPT only
    strike: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    right: Mapped[str | None] = mapped_column(String(1))         # C | P
    multiplier: Mapped[int | None] = mapped_column(BigInteger)

class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    broker_account_id: Mapped[int] = mapped_column(ForeignKey("broker_accounts.id"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    avg_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    last_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    prev_close_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("broker_account_id", "instrument_id"),)

class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    taken_on: Mapped[date] = mapped_column(Date, unique=True)
    total_value_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    cash_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    per_account: Mapped[dict] = mapped_column(JSONB, default=dict)

class CashFlow(Base):
    __tablename__ = "cash_flows"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    broker_account_id: Mapped[int] = mapped_column(ForeignKey("broker_accounts.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[FlowKind] = mapped_column(Enum(FlowKind, name="flow_kind"))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2))   # signed: + in, - out
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    amount_local: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    source_ref: Mapped[str | None] = mapped_column(String(128), unique=True)  # broker txn id, idempotent ingest

class Basket(Base):
    __tablename__ = "baskets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    thesis: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)          # e.g. conversation/session id
    horizon: Mapped[str | None] = mapped_column(Text)
    invalidation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="open")  # open | closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class BasketAllocation(Base):
    __tablename__ = "basket_allocations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("baskets.id"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    cost_basis_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))  # captured at allocation time
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("basket_id", "instrument_id"),)

class BasketSnapshot(Base):
    __tablename__ = "basket_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    basket_id: Mapped[int] = mapped_column(ForeignKey("baskets.id"))
    taken_on: Mapped[date] = mapped_column(Date)
    value_usd: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    __table_args__ = (UniqueConstraint("basket_id", "taken_on"),)

class JournalEntry(Base):
    """Owner's trade journal: the searchable 'why' behind positions.

    Anchored to symbol (ticker or OCC), not instrument_id, so entries outlive
    closed/vanished positions — the closed-position story stays readable.
    Owner-only at the web layer: notes and target/stop are free-form dollars
    that masking cannot reliably scrub.
    """
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(320))
    tag: Mapped[str] = mapped_column(String(40))                  # e.g. thesis, trim, roll, hedge
    note: Mapped[str] = mapped_column(Text)
    target_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    stop_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    confidence: Mapped[int | None] = mapped_column(SmallInteger)  # 1-5
    source_ref: Mapped[str | None] = mapped_column(String(128))   # e.g. conversation/session id
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(note,'') || ' ' || "
                 "coalesce(tag,'') || ' ' || coalesce(symbol,''))", persisted=True),
    )

class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(320))               # email or "system"
    category: Mapped[str] = mapped_column(String(64))             # e.g. auth.login, gateway.disconnect
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
