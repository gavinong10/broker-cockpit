# Phase 0 — Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> or superpowers:executing-plans to implement task-by-task.

**Goal:** Stand up the broker-cockpit skeleton: repo + docker-compose, Postgres schema v1 with migrations, Google login with owner/viewer roles, IB Gateway (paper mode) connected from the Python worker with health alerting, nightly off-site backups with a verified restore drill, and a VPS deployment runbook.

**Architecture:** Monorepo with `apps/web` (Next.js 15 App Router + Auth.js v5, JWT sessions, DB email-allowlist) and `apps/worker` (Python 3.12 FastAPI, SQLAlchemy 2 + Alembic owns all migrations, ib_async to a dockerized IB Gateway). docker-compose orchestrates `web`, `worker`, `ib-gateway`, `postgres`, `backup`, and (prod only) `caddy` for TLS. Only caddy/web are publicly exposed; worker and gateway live on the internal docker network.

**Tech Stack:** Next.js 15 (TypeScript, App Router), Auth.js v5 (Google provider), drizzle-orm (web reads), Python 3.12 + uv, FastAPI, SQLAlchemy 2 + Alembic, ib_async, gnzsnz/ib-gateway docker image (IBC auto-restart), Postgres 16, rclone → Backblaze B2, Caddy.

## Global Constraints (from spec)

- Browser never sees broker credentials or talks to brokers; worker is never publicly exposed.
- Broker credentials live only in `.env`/docker secrets — never in DB, never committed.
- All broker-affecting and auth events land in the append-only `audit_log`.
- Role enforcement is server-side on every route/action; `viewer` = read-only.
- Development and initial deployment run against the IBKR **paper** account; live headless user is a deploy-time env swap only.
- Every actual call to a paid/authenticated external API during execution requires explicit per-action user OK (standing user rule) — gateway logins and B2 uploads are run/approved by the user.
- Exit criteria: Google login works with role enforcement; gateway survives one week unattended (soak task); restore drill passes.

---

## Task 0 — Human prerequisites (user-only; parallel with Tasks 1–4)

No code. These block Tasks 5–8; start them now.

- [ ] **IBKR paper account**: In Client Portal → Settings → Account Settings → Paper Trading Account, note paper username; reset paper password. (Used for all dev.)
- [ ] **IBKR headless secondary user (live, for deploy later)**: Client Portal → Settings → Users & Access Rights → Add User. Grant **trading permissions only** — no funding, no withdrawals, no settings. After creation, opt the new user out of the Secure Login System (Settings → Secure Login System → SLS opt-out for that user). Record username/password in your password manager only.
- [ ] **Google OAuth client**: console.cloud.google.com → new project `broker-cockpit` → OAuth consent screen (External, add your email + guest emails as test users) → Credentials → Create OAuth client ID (Web application). Authorized redirect URIs: `http://localhost:3000/api/auth/callback/google` and `https://<your-domain>/api/auth/callback/google`. Record client ID + secret.
- [ ] **VPS**: provision Ubuntu 24.04 (Hetzner CX22 or equivalent, ~$8/mo), note IP; point a DNS A record `cockpit.<your-domain>` at it.
- [ ] **Backblaze B2**: create bucket `broker-cockpit-backups` (private), create an application key scoped to that bucket. Record keyID/applicationKey.
- [ ] **Discord**: create webhook URL in your alerts channel for health/heartbeat pings.

---

## Task 1 — Repo scaffold + compose skeleton

**Files:**
- Create: `.gitignore`, `.env.example`, `compose.yml`, `apps/worker/pyproject.toml`, `apps/worker/app/__init__.py`, `apps/worker/app/main.py`, `apps/worker/app/config.py`, `apps/worker/Dockerfile`, `apps/worker/tests/test_health.py`, `apps/web/**` (generated), `apps/web/Dockerfile`
- Produces: running `postgres` + `worker` + `web` via compose; `GET worker:8000/health` → `{"db":"ok"}`.

**Steps:**

- [ ] Init repo:
  ```bash
  cd ~/dev/claude-projects/broker-cockpit && git init -b main
  ```
- [ ] Write `.gitignore`:
  ```
  node_modules/
  .next/
  __pycache__/
  .venv/
  .env
  *.pyc
  .DS_Store
  backups/
  ```
- [ ] Write `.env.example` (every var the stack needs; `.env` is copied from this and filled locally, never committed):
  ```
  POSTGRES_USER=cockpit
  POSTGRES_PASSWORD=change-me
  POSTGRES_DB=cockpit
  DATABASE_URL=postgresql+psycopg://cockpit:change-me@postgres:5432/cockpit
  WEB_DATABASE_URL=postgresql://cockpit:change-me@postgres:5432/cockpit
  AUTH_SECRET=generate-with-openssl-rand-base64-33
  AUTH_GOOGLE_ID=
  AUTH_GOOGLE_SECRET=
  OWNER_EMAIL=gavinong10@gmail.com
  INTERNAL_API_TOKEN=generate-with-openssl-rand-hex-32
  IB_USER=
  IB_PASSWORD=
  IB_TRADING_MODE=paper
  DISCORD_WEBHOOK_URL=
  B2_KEY_ID=
  B2_APP_KEY=
  B2_BUCKET=broker-cockpit-backups
  ```
- [ ] Scaffold web app:
  ```bash
  cd apps && npx create-next-app@latest web --ts --app --tailwind --eslint --src-dir --no-import-alias --use-npm && cd ..
  ```
  Expect: `Success! Created web`.
- [ ] Write `apps/worker/pyproject.toml`:
  ```toml
  [project]
  name = "cockpit-worker"
  version = "0.1.0"
  requires-python = ">=3.12"
  dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "psycopg[binary]>=3.2",
    "pydantic-settings>=2.4",
    "httpx>=0.27",
    "ib_async>=1.0.1",
  ]
  [dependency-groups]
  dev = ["pytest>=8", "pytest-asyncio>=0.24", "httpx>=0.27"]
  ```
- [ ] Write `apps/worker/app/config.py`:
  ```python
  from pydantic_settings import BaseSettings

  class Settings(BaseSettings):
      database_url: str
      internal_api_token: str = "dev-token"
      ib_gateway_host: str = "ib-gateway"
      ib_gateway_port: int = 4004
      ib_client_id: int = 11
      discord_webhook_url: str = ""

  settings = Settings()  # reads env vars
  ```
- [ ] Write failing test `apps/worker/tests/test_health.py`:
  ```python
  from fastapi.testclient import TestClient
  from app.main import app

  def test_health_reports_db(monkeypatch):
      from app import main
      monkeypatch.setattr(main, "check_db", lambda: "ok")
      client = TestClient(app)
      r = client.get("/health")
      assert r.status_code == 200
      assert r.json()["db"] == "ok"
  ```
- [ ] Verify failure: `cd apps/worker && uv run pytest` → `ModuleNotFoundError: No module named 'app.main'`.
- [ ] Write `apps/worker/app/main.py`:
  ```python
  from fastapi import FastAPI
  from sqlalchemy import create_engine, text
  from app.config import settings

  app = FastAPI()
  _engine = None

  def get_engine():
      global _engine
      if _engine is None:
          _engine = create_engine(settings.database_url, pool_pre_ping=True)
      return _engine

  def check_db() -> str:
      try:
          with get_engine().connect() as conn:
              conn.execute(text("SELECT 1"))
          return "ok"
      except Exception:
          return "down"

  @app.get("/health")
  def health():
      return {"db": check_db(), "gateway": "not-configured"}
  ```
- [ ] Verify pass: `uv run pytest` → `1 passed`.
- [ ] Write `apps/worker/Dockerfile`:
  ```dockerfile
  FROM python:3.12-slim
  COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
  WORKDIR /srv
  COPY pyproject.toml uv.lock* ./
  RUN uv sync --no-dev --frozen || uv sync --no-dev
  COPY app ./app
  COPY alembic.ini* ./
  COPY migrations ./migrations
  CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
  ```
  (migrations dir arrives in Task 2; create empty `apps/worker/migrations/.gitkeep` now so the build succeeds.)
- [ ] Write `apps/web/Dockerfile`:
  ```dockerfile
  FROM node:22-slim AS build
  WORKDIR /srv
  COPY package*.json ./
  RUN npm ci
  COPY . .
  RUN npm run build
  FROM node:22-slim
  WORKDIR /srv
  COPY --from=build /srv/.next/standalone ./
  COPY --from=build /srv/.next/static ./.next/static
  EXPOSE 3000
  CMD ["node", "server.js"]
  ```
  Add `output: "standalone"` to `apps/web/next.config.ts`.
- [ ] Write `compose.yml`:
  ```yaml
  services:
    postgres:
      image: postgres:16
      env_file: .env
      volumes: ["pgdata:/var/lib/postgresql/data"]
      healthcheck:
        test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER}"]
        interval: 10s
        retries: 5
    worker:
      build: apps/worker
      env_file: .env
      depends_on:
        postgres: { condition: service_healthy }
    web:
      build: apps/web
      env_file: .env
      ports: ["3000:3000"]
      depends_on: [worker]
  volumes:
    pgdata:
  ```
- [ ] Verify: `cp .env.example .env` (fill POSTGRES_*, AUTH_SECRET, INTERNAL_API_TOKEN), then `docker compose up -d --build postgres worker` and `docker compose exec worker uv run python -c "import httpx; print(httpx.get('http://localhost:8000/health').json())"` → `{'db': 'ok', 'gateway': 'not-configured'}`.
- [ ] Commit: `git add -A && git commit -m "Task 1: repo scaffold, compose skeleton, worker health endpoint"`.

---

## Task 2 — Schema v1 + Alembic migrations

**Files:**
- Create: `apps/worker/alembic.ini`, `apps/worker/migrations/env.py`, `apps/worker/migrations/versions/0001_schema_v1.py`, `apps/worker/app/models.py`, `apps/worker/tests/test_schema.py`
- Interfaces produced: tables `users`, `broker_accounts`, `instruments`, `positions`, `snapshots`, `cash_flows`, `audit_log`. (Journal/rules/proposals tables arrive in their own phases via new migrations — YAGNI now.)

**Steps:**

- [ ] Write `apps/worker/app/models.py` (SQLAlchemy 2 declarative; authoritative schema):
  ```python
  import enum
  from datetime import datetime, date
  from decimal import Decimal
  from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Enum, ForeignKey,
                          Numeric, String, Text, UniqueConstraint, func)
  from sqlalchemy.dialects.postgresql import JSONB
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

  class AuditLog(Base):
      __tablename__ = "audit_log"
      id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
      at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
      actor: Mapped[str] = mapped_column(String(320))               # email or "system"
      category: Mapped[str] = mapped_column(String(64))             # e.g. auth.login, gateway.disconnect
      payload: Mapped[dict] = mapped_column(JSONB, default=dict)
  ```
- [ ] Init Alembic: `cd apps/worker && uv run alembic init migrations`; in `migrations/env.py` set `target_metadata = Base.metadata` (import from `app.models`) and read `sqlalchemy.url` from `DATABASE_URL` env; in `alembic.ini` blank the url line.
- [ ] Generate migration: `uv run alembic revision --autogenerate -m "schema v1"` → creates `migrations/versions/xxxx_schema_v1.py`; review it contains all 7 tables.
- [ ] Write failing test `apps/worker/tests/test_schema.py`:
  ```python
  import os
  import pytest
  from sqlalchemy import create_engine, inspect

  pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs postgres")

  EXPECTED = {"users", "broker_accounts", "instruments", "positions",
              "snapshots", "cash_flows", "audit_log"}

  def test_migration_creates_all_tables():
      eng = create_engine(os.environ["TEST_DATABASE_URL"])
      assert EXPECTED <= set(inspect(eng).get_table_names())
  ```
- [ ] Verify failure then pass: with compose postgres up, `docker compose exec worker uv run alembic upgrade head`, then
  `TEST_DATABASE_URL=postgresql+psycopg://cockpit:<pw>@localhost:5432/cockpit uv run pytest tests/test_schema.py` (add `ports: ["5432:5432"]` to postgres service for local dev) → `1 passed`.
- [ ] Add migration-on-boot to worker service command in `compose.yml`:
  ```yaml
      command: sh -c "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"
  ```
- [ ] Seed owner row (idempotent) — append to worker boot in `app/main.py` startup hook:
  ```python
  import os
  from sqlalchemy import text

  @app.on_event("startup")
  def seed_owner():
      email = os.environ.get("OWNER_EMAIL")
      if not email:
          return
      with get_engine().begin() as conn:
          conn.execute(text(
              "INSERT INTO users (email, role) VALUES (:e, 'owner') "
              "ON CONFLICT (email) DO NOTHING"), {"e": email})
  ```
- [ ] Verify: `docker compose up -d --build worker` then `docker compose exec postgres psql -U cockpit -c "SELECT email, role FROM users;"` → one row, `gavinong10@gmail.com | owner`.
- [ ] Commit: `git commit -am "Task 2: schema v1, alembic migrations, owner seed"`.

---

## Task 3 — Google login + role enforcement (web)

**Files:**
- Create: `apps/web/src/auth.ts`, `apps/web/src/db.ts`, `apps/web/src/middleware.ts`, `apps/web/src/app/api/auth/[...nextauth]/route.ts`, `apps/web/src/app/login/page.tsx`, `apps/web/src/app/denied/page.tsx`, `apps/web/src/lib/roles.ts`, `apps/web/src/lib/roles.test.ts`
- Modify: `apps/web/src/app/page.tsx` (show email + role after login)
- Consumes: `users` table (Task 2). Produces: `auth()` session with `role`; `requireOwner()` guard for later phases.

**Steps:**

- [ ] Install deps: `cd apps/web && npm i next-auth@beta drizzle-orm pg && npm i -D vitest @types/pg`.
- [ ] Write `apps/web/src/db.ts`:
  ```ts
  import { Pool } from "pg";
  export const pool = new Pool({ connectionString: process.env.WEB_DATABASE_URL });
  export async function getUserRole(email: string): Promise<"owner" | "viewer" | null> {
    const r = await pool.query("SELECT role FROM users WHERE email = $1", [email]);
    return r.rows[0]?.role ?? null;
  }
  ```
- [ ] Write failing test `apps/web/src/lib/roles.test.ts`:
  ```ts
  import { describe, expect, it } from "vitest";
  import { canWrite, canRead } from "./roles";

  describe("role guards", () => {
    it("owner can read and write", () => {
      expect(canRead("owner")).toBe(true);
      expect(canWrite("owner")).toBe(true);
    });
    it("viewer can read, not write", () => {
      expect(canRead("viewer")).toBe(true);
      expect(canWrite("viewer")).toBe(false);
    });
    it("null role can do nothing", () => {
      expect(canRead(null)).toBe(false);
      expect(canWrite(null)).toBe(false);
    });
  });
  ```
- [ ] Verify failure: `npx vitest run` → cannot resolve `./roles`.
- [ ] Write `apps/web/src/lib/roles.ts`:
  ```ts
  export type Role = "owner" | "viewer" | null;
  export const canRead = (r: Role) => r === "owner" || r === "viewer";
  export const canWrite = (r: Role) => r === "owner";
  ```
- [ ] Verify pass: `npx vitest run` → `3 passed`.
- [ ] Write `apps/web/src/auth.ts` — allowlist enforced at sign-in, role embedded in JWT:
  ```ts
  import NextAuth from "next-auth";
  import Google from "next-auth/providers/google";
  import { getUserRole } from "./db";

  export const { handlers, auth, signIn, signOut } = NextAuth({
    providers: [Google],
    session: { strategy: "jwt" },
    callbacks: {
      async signIn({ user }) {
        return (await getUserRole(user.email ?? "")) !== null; // not allowlisted -> rejected
      },
      async jwt({ token }) {
        if (token.email) token.role = await getUserRole(token.email);
        return token;
      },
      async session({ session, token }) {
        (session.user as any).role = token.role ?? null;
        return session;
      },
    },
    pages: { signIn: "/login", error: "/denied" },
  });
  ```
- [ ] Write `apps/web/src/app/api/auth/[...nextauth]/route.ts`:
  ```ts
  import { handlers } from "@/../src/auth";
  export const { GET, POST } = handlers;
  ```
- [ ] Write `apps/web/src/middleware.ts` — every route requires a session except login/denied/auth:
  ```ts
  export { auth as middleware } from "./auth";
  export const config = { matcher: ["/((?!api/auth|login|denied|_next|favicon.ico).*)"] };
  ```
  In `auth.ts` add an `authorized` callback: `authorized: ({ auth }) => !!auth?.user`.
- [ ] Write minimal `login/page.tsx` (one Google button via `signIn("google")` server action) and `denied/page.tsx` ("This instance is invite-only."). Replace `app/page.tsx` body with a server component that calls `auth()` and renders `Signed in as {email} ({role})`.
- [ ] Verify manually: `docker compose up -d --build web` → visit `http://localhost:3000` → redirected to `/login` → Google sign-in with OWNER_EMAIL → home shows `(owner)`. Sign in with a non-allowlisted Google account → lands on `/denied`.
- [ ] Log auth events: in `signIn` callback, `INSERT INTO audit_log (actor, category, payload)` with `auth.login` / `auth.rejected` via `pool.query`.
- [ ] Commit: `git commit -am "Task 3: Google auth, allowlist roles, middleware, audit of logins"`.

---

## Task 4 — Web ⇄ worker internal API auth

**Files:**
- Create: `apps/worker/app/internal_auth.py`, `apps/worker/tests/test_internal_auth.py`
- Modify: `apps/worker/app/main.py` (protected `/internal/ping`), `apps/web/src/lib/worker.ts` (typed fetch helper)
- Produces: `workerFetch(path)` for all later phases; worker rejects unauthenticated internal calls.

**Steps:**

- [ ] Write failing test `apps/worker/tests/test_internal_auth.py`:
  ```python
  from fastapi.testclient import TestClient
  from app.main import app

  def test_internal_requires_token():
      c = TestClient(app)
      assert c.get("/internal/ping").status_code == 401
      ok = c.get("/internal/ping", headers={"X-Internal-Token": "dev-token"})
      assert ok.status_code == 200 and ok.json() == {"pong": True}
  ```
- [ ] Verify failure: `uv run pytest tests/test_internal_auth.py` → 404 ≠ 401.
- [ ] Write `apps/worker/app/internal_auth.py`:
  ```python
  import hmac
  from fastapi import Header, HTTPException
  from app.config import settings

  def require_internal(x_internal_token: str = Header(default="")):
      if not hmac.compare_digest(x_internal_token, settings.internal_api_token):
          raise HTTPException(status_code=401)
  ```
  In `main.py`: `@app.get("/internal/ping", dependencies=[Depends(require_internal)])` returning `{"pong": True}`.
- [ ] Verify pass: `uv run pytest` → all green.
- [ ] Write `apps/web/src/lib/worker.ts`:
  ```ts
  export async function workerFetch(path: string, init: RequestInit = {}) {
    const res = await fetch(`http://worker:8000${path}`, {
      ...init,
      headers: { ...init.headers, "X-Internal-Token": process.env.INTERNAL_API_TOKEN! },
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`worker ${path}: ${res.status}`);
    return res.json();
  }
  ```
- [ ] Verify end-to-end: temporary server component call of `workerFetch("/internal/ping")` on home page renders `pong` — then remove it.
- [ ] Commit: `git commit -am "Task 4: internal API token auth between web and worker"`.

---

## Task 5 — IB Gateway (paper) + worker connection + disconnect alerting

**Files:**
- Create: `apps/worker/app/ibkr.py`, `apps/worker/app/notify.py`, `apps/worker/tests/test_notify.py`
- Modify: `compose.yml` (ib-gateway service), `apps/worker/app/main.py` (health includes gateway; startup connects)
- Consumes: `.env` `IB_USER`/`IB_PASSWORD` (paper creds; user fills — never committed). Produces: connected `IB` client for Phase 1.

**Steps:**

- [ ] Add gateway service to `compose.yml`:
  ```yaml
    ib-gateway:
      image: ghcr.io/gnzsnz/ib-gateway:stable
      env_file: .env
      environment:
        TWS_USERID: ${IB_USER}
        TWS_PASSWORD: ${IB_PASSWORD}
        TRADING_MODE: ${IB_TRADING_MODE}
        AUTO_RESTART_TIME: "11:59 PM"
        RELOGIN_AFTER_TWOFA_TIMEOUT: "yes"
      restart: unless-stopped
  ```
  (Paper port inside the gnzsnz container network: 4004; live: 4003. `settings.ib_gateway_port` default 4004 matches paper.)
- [ ] Write failing test `apps/worker/tests/test_notify.py`:
  ```python
  from app.notify import discord_message

  def test_discord_message_shape():
      body = discord_message("gateway.disconnect", "IB Gateway disconnected")
      assert body["embeds"][0]["title"] == "gateway.disconnect"
      assert "disconnected" in body["embeds"][0]["description"]
  ```
- [ ] Verify failure, then write `apps/worker/app/notify.py`:
  ```python
  import httpx
  from app.config import settings

  def discord_message(title: str, description: str) -> dict:
      return {"embeds": [{"title": title, "description": description}]}

  def alert(title: str, description: str) -> None:
      if not settings.discord_webhook_url:
          return
      try:
          httpx.post(settings.discord_webhook_url,
                     json=discord_message(title, description), timeout=10)
      except httpx.HTTPError:
          pass  # alerting must never crash the worker
  ```
  Verify pass: `uv run pytest tests/test_notify.py` → `1 passed`.
- [ ] Write `apps/worker/app/ibkr.py`:
  ```python
  import asyncio
  from ib_async import IB
  from app.config import settings
  from app.notify import alert

  class Gateway:
      def __init__(self) -> None:
          self.ib = IB()
          self.ib.disconnectedEvent += self._on_disconnect

      @property
      def connected(self) -> bool:
          return self.ib.isConnected()

      async def connect_forever(self) -> None:
          delay = 5
          while True:
              if not self.connected:
                  try:
                      await self.ib.connectAsync(settings.ib_gateway_host,
                                                 settings.ib_gateway_port,
                                                 clientId=settings.ib_client_id)
                      alert("gateway.connected", "IB Gateway session established")
                      delay = 5
                  except Exception as e:
                      alert("gateway.connect_failed", f"{type(e).__name__}: {e}") if delay > 60 else None
                      delay = min(delay * 2, 300)
              await asyncio.sleep(delay)

      def _on_disconnect(self) -> None:
          alert("gateway.disconnect", "IB Gateway disconnected — reconnect loop engaged")

  gateway = Gateway()
  ```
- [ ] Wire into `main.py`: startup creates `asyncio.create_task(gateway.connect_forever())`; `/health` returns `{"db": check_db(), "gateway": "connected" if gateway.connected else "down"}`. Also log `gateway.disconnect` to `audit_log`.
- [ ] **[USER ACTION — paper credentials + first login approval]** Fill `IB_USER`/`IB_PASSWORD` (paper) in `.env`, then `docker compose up -d --build ib-gateway worker`. Watch `docker compose logs -f ib-gateway` for `IBC: Login has completed`.
- [ ] Verify: `docker compose exec worker uv run python -c "import httpx; print(httpx.get('http://localhost:8000/health').json())"` → `{'db': 'ok', 'gateway': 'connected'}`.
- [ ] Verify resilience: `docker compose restart ib-gateway`; within ~2 min health returns to `connected`; Discord shows disconnect + reconnect embeds.
- [ ] Commit: `git commit -am "Task 5: IB gateway service, reconnect loop, Discord alerting"`.

---

## Task 6 — Nightly backups + restore drill

**Files:**
- Create: `infra/backup/Dockerfile`, `infra/backup/backup.sh`, `infra/backup/crontab`, `scripts/restore-drill.sh`, `docs/RESTORE.md`
- Modify: `compose.yml` (backup service)
- Produces: nightly `pg_dump` gz in B2 with 30-day retention; a drill script proving restores work.

**Steps:**

- [ ] Write `infra/backup/backup.sh`:
  ```bash
  #!/bin/sh
  set -eu
  STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
  FILE="/tmp/cockpit-${STAMP}.sql.gz"
  pg_dump -h postgres -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$FILE"
  rclone copyto "$FILE" ":b2:${B2_BUCKET}/cockpit-${STAMP}.sql.gz" \
    --b2-account "$B2_KEY_ID" --b2-key "$B2_APP_KEY"
  rclone delete ":b2:${B2_BUCKET}/" --min-age 30d \
    --b2-account "$B2_KEY_ID" --b2-key "$B2_APP_KEY"
  rm -f "$FILE"
  echo "backup ok: cockpit-${STAMP}.sql.gz"
  ```
- [ ] Write `infra/backup/Dockerfile`:
  ```dockerfile
  FROM alpine:3.20
  RUN apk add --no-cache postgresql16-client rclone dcron
  COPY backup.sh /usr/local/bin/backup.sh
  COPY crontab /etc/crontabs/root
  RUN chmod +x /usr/local/bin/backup.sh
  CMD ["crond", "-f", "-l", "2"]
  ```
  `infra/backup/crontab`: `15 8 * * * PGPASSWORD=$POSTGRES_PASSWORD /usr/local/bin/backup.sh` (08:15 UTC nightly).
- [ ] Add to `compose.yml`:
  ```yaml
    backup:
      build: infra/backup
      env_file: .env
      environment: { PGPASSWORD: "${POSTGRES_PASSWORD}" }
      depends_on: [postgres]
  ```
- [ ] **[USER ACTION — B2 credentials + first upload approval]** Fill B2 vars in `.env`; run one backup manually: `docker compose run --rm backup sh -c 'PGPASSWORD=$POSTGRES_PASSWORD backup.sh'` → `backup ok: cockpit-<stamp>.sql.gz`; confirm object visible in B2 UI.
- [ ] Write `scripts/restore-drill.sh` (proves a backup restores into a scratch DB and row counts match):
  ```bash
  #!/bin/sh
  set -eu
  LATEST=$(docker compose run --rm backup rclone lsf ":b2:${B2_BUCKET}/" \
    --b2-account "$B2_KEY_ID" --b2-key "$B2_APP_KEY" | sort | tail -1)
  docker compose run --rm backup sh -c \
    "rclone cat ':b2:${B2_BUCKET}/${LATEST}' --b2-account $B2_KEY_ID --b2-key $B2_APP_KEY" \
    > /tmp/drill.sql.gz
  docker compose exec -T postgres psql -U "$POSTGRES_USER" -c "DROP DATABASE IF EXISTS drill; CREATE DATABASE drill;"
  gunzip -c /tmp/drill.sql.gz | docker compose exec -T postgres psql -U "$POSTGRES_USER" -d drill
  LIVE=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM users")
  DRILL=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d drill -tAc "SELECT count(*) FROM users")
  [ "$LIVE" = "$DRILL" ] && echo "RESTORE DRILL PASS (users: $LIVE)" || { echo "FAIL: $LIVE != $DRILL"; exit 1; }
  ```
- [ ] Run drill (env vars sourced from `.env`): `sh scripts/restore-drill.sh` → `RESTORE DRILL PASS (users: 1)`.
- [ ] Write `docs/RESTORE.md`: full cold-restore procedure (new VPS → install docker → clone repo → fill `.env` from password manager → pull latest B2 dump → restore → `compose up`), plus drill cadence (quarterly).
- [ ] Commit: `git commit -am "Task 6: nightly B2 backups, restore drill script + runbook"`.

---

## Task 7 — VPS deployment + TLS

**Files:**
- Create: `compose.prod.yml` (caddy overlay, no public web port), `infra/caddy/Caddyfile`, `docs/DEPLOY.md`
- Consumes: Task 0 VPS + DNS. Produces: `https://cockpit.<domain>` serving the app.

**Steps:**

- [ ] Write `infra/caddy/Caddyfile`:
  ```
  {$COCKPIT_DOMAIN} {
      reverse_proxy web:3000
  }
  ```
- [ ] Write `compose.prod.yml`:
  ```yaml
  services:
    web:
      ports: !reset []
    caddy:
      image: caddy:2
      env_file: .env
      ports: ["80:80", "443:443"]
      volumes:
        - ./infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro
        - caddy_data:/data
      depends_on: [web]
  volumes:
    caddy_data:
  ```
  Add `COCKPIT_DOMAIN=` to `.env.example`.
- [ ] Write `docs/DEPLOY.md`: ufw allow 22/80/443 only; install docker; clone repo; `cp .env.example .env` and fill (paper IB creds first — live headless swap is a later, deliberate step); `docker compose -f compose.yml -f compose.prod.yml up -d --build`; update Google OAuth redirect URI with the prod domain.
- [ ] **[USER ACTION]** Execute DEPLOY.md on the VPS.
- [ ] Verify: `curl -s https://$COCKPIT_DOMAIN/login | grep -i google` → login markup; full Google login round-trip works in browser; `/health` (via `docker compose exec worker …`) shows `db: ok, gateway: connected`.
- [ ] Commit: `git commit -am "Task 7: prod compose overlay with caddy TLS, deploy runbook"`.

---

## Task 8 — One-week soak (exit gate)

**Files:**
- Modify: `apps/worker/app/main.py` (daily heartbeat)
- Produces: Phase 0 exit evidence.

**Steps:**

- [ ] Add daily heartbeat to worker (asyncio task, fires 21:00 UTC): posts Discord embed `heartbeat` with `{db, gateway, uptime_hours}` and writes `system.heartbeat` to `audit_log`.
- [ ] Verify: force-run once (temporary interval=60s, observe embed, revert).
- [ ] Commit: `git commit -am "Task 8: daily heartbeat"`.
- [ ] **[ELAPSED-TIME GATE]** Observe 7 consecutive daily heartbeats with `gateway: connected` and no manual intervention (auto-restart window included). Any disconnect embed that self-heals is acceptable; any manual re-login is a soak failure → diagnose, fix, restart the 7-day clock.
- [ ] On pass: record soak evidence (screenshot or audit_log query) in `docs/DEPLOY.md` § "Phase 0 exit", and mark Phase 0 complete in the spec.

---

## Self-review

- **Spec coverage:** VPS+compose → T1/T7; headless IBKR auth → T0 (user) + T5 (paper) with live swap documented in T7/DEPLOY.md; Google login w/ roles → T3; DB schema → T2; backups + restore drill → T6; week-unattended gate → T8. §3 trust boundaries hold: only caddy exposed in prod (`ports: !reset []`), worker/gateway internal, secrets only in `.env`.
- **Placeholders:** none — every step has code or an exact command with expected output. Two intentional generator steps (create-next-app, alembic init) rely on tool output rather than pasted boilerplate.
- **Consistency:** `INTERNAL_API_TOKEN` name matches across `.env.example`, `config.py`, `worker.ts`; paper port 4004 matches `config.py` default; `users` table shape matches Task 3's `getUserRole` query; `OWNER_EMAIL` seed matches allowlist flow.
