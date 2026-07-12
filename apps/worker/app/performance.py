"""Flow-adjusted performance engine (design spec §4.1).

Honest account-growth + ROI, grounded on the owner's real dated external cash
flows. Robinhood's "value vs net contributions" line breaks under withdrawals
(deposit $100k -> +$100k profit -> withdraw $100k reads as nonsense); we compute
three clearly-labelled measures instead:

  - dollar_pnl        : the headline. Exact. current_value + Σwithdrawals − Σdeposits.
  - money_weighted_return (XIRR) : "what did my dollars earn given contribution
                        timing." Exact given flows + today's real value.
  - twr               : time-weighted return, chain-linked from the ESTIMATED
                        daily value series. SECONDARY + labelled estimated.

FLOW SIGN CONVENTION (single, module-wide — read this before touching anything):
Flows use the INVESTOR / cash-ledger convention, i.e. from the owner's pocket:

    deposit      -> NEGATIVE amount   (cash leaves the pocket, into the account)
    withdrawal   -> POSITIVE amount   (cash returns to the pocket)
    terminal value at end -> POSITIVE inflow (as if liquidated today)

This is the opposite sign of the DB `cash_flows.amount_usd` column (which stores
+deposit / −withdrawal, i.e. account-side). Callers convert with a single
negation: investor_amount = −amount_usd. Keeping ONE convention in this module
(and negating at the boundary) is deliberate — mixing conventions is exactly the
subtle-sign-bug class this engine exists to avoid.

Only EXTERNAL flows (deposits, withdrawals, ACATS) are flows. Dividends/interest
are returns, never contributions, and must not be passed in here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

Flow = tuple[date, Decimal]          # (date, investor-convention amount)
DailyValue = tuple[date, Decimal]    # (date, portfolio value that day)

_DAYS_PER_YEAR = 365.0
# XIRR bracket. Lower bound keeps (1+r) strictly positive (0.0001); upper bound
# caps runaway annualized rates on very short windows.
_RATE_LO = -0.9999
_RATE_HI = 100.0
_NPV_TOL = 1e-9
_ITERS = 200


# --- money-weighted return (XIRR) ---------------------------------------------

def _xnpv(rate: float, cashflows: list[tuple[date, float]], t0: date) -> float:
    """Net present value of dated cashflows at ``rate`` (annual), t0 = epoch."""
    total = 0.0
    base = 1.0 + rate
    for d, amt in cashflows:
        years = (d - t0).days / _DAYS_PER_YEAR
        total += amt / (base ** years)
    return total


def money_weighted_return(
    flows: list[Flow], end_value, end_date: date
) -> float | None:
    """Annualized money-weighted return (XIRR) over external flows + terminal value.

    ``flows`` are investor-convention (deposits negative, withdrawals positive).
    ``end_value`` (the current portfolio value) is appended as a positive inflow
    at ``end_date``. Solved by bisection on the NPV, which is strictly monotonic
    in ``rate`` for the normal one-sign-change investment pattern, so the bracket
    always contains exactly one root.

    Returns a decimal rate (0.10 == 10%/yr), or ``None`` for degenerate inputs
    (no flows, only one sign, or a zero-length time span) — never divides by zero.
    """
    cashflows: list[tuple[date, float]] = [(d, float(a)) for d, a in flows]
    cashflows.append((end_date, float(end_value)))

    amounts = [a for _, a in cashflows]
    # Need at least one inflow and one outflow for an IRR to exist.
    if not any(a > 0 for a in amounts) or not any(a < 0 for a in amounts):
        return None

    dates = [d for d, _ in cashflows]
    t0 = min(dates)
    if (max(dates) - t0).days == 0:
        return None  # all same day -> annualized rate undefined

    f_lo = _xnpv(_RATE_LO, cashflows, t0)
    f_hi = _xnpv(_RATE_HI, cashflows, t0)
    if f_lo == 0.0:
        return _RATE_LO
    if f_hi == 0.0:
        return _RATE_HI
    if (f_lo > 0) == (f_hi > 0):
        return None  # no sign change in the bracket -> no root here

    lo, hi = _RATE_LO, _RATE_HI
    for _ in range(_ITERS):
        mid = (lo + hi) / 2.0
        f_mid = _xnpv(mid, cashflows, t0)
        if abs(f_mid) < _NPV_TOL:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --- dollar P&L + net contributions (exact) -----------------------------------

def dollar_pnl(flows: list[Flow], current_value) -> Decimal:
    """Net dollar P&L = current_value + Σ(investor flows).

    Since deposits are negative and withdrawals positive, Σ(flows) equals
    Σwithdrawals − Σdeposits, so this is current_value + Σwithdrawals − Σdeposits
    — the true profit regardless of deposit/withdrawal timing. Exact.
    """
    total = Decimal(str(current_value))
    for _, amount in flows:
        total += Decimal(amount)
    return total


def net_contributions(flows: list[Flow]) -> Decimal:
    """Net capital contributed = Σdeposits − Σwithdrawals = −Σ(investor flows)."""
    total = Decimal("0")
    for _, amount in flows:
        total -= Decimal(amount)
    return total


def net_contributions_series(flows: list[Flow]) -> list[tuple[date, Decimal]]:
    """Cumulative net contributions by date (one point per distinct flow date).

    Each step adds −amount, so a deposit steps the line UP and a withdrawal steps
    it DOWN — the overlay reads as capital in/out, never as performance.
    """
    by_date: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for d, amount in flows:
        by_date[d] -= Decimal(amount)
    out: list[tuple[date, Decimal]] = []
    running = Decimal("0")
    for d in sorted(by_date):
        running += by_date[d]
        out.append((d, running))
    return out


# --- time-weighted return (estimated; secondary) ------------------------------

def twr(daily_values: list[DailyValue], flows: list[Flow]) -> float | None:
    """Annualized time-weighted return, chain-linked across the daily series.

    Per-step return r_t = (V_t − flow_t) / V_{t−1}, where flow_t is the net
    ACCOUNT-side external flow landing in (prev_date, cur_date] (deposit +,
    withdrawal −) — the end-of-day convention removes contributions so a deposit
    never reads as same-day gain. Steps where V_{t−1} == 0 (or the step return is
    non-positive, i.e. value wiped) pause the index rather than divide by zero.

    Consumes the ESTIMATED daily value series, so its result is estimated and
    must be labelled as such by the caller. Returns a decimal rate or ``None``.
    """
    if len(daily_values) < 2:
        return None

    acct_flow: dict[date, float] = defaultdict(float)
    for d, amount in flows:
        acct_flow[d] += -float(amount)  # investor -> account convention

    factor = 1.0
    used = False
    for i in range(1, len(daily_values)):
        d_prev, v_prev = daily_values[i - 1]
        d_cur, v_cur = daily_values[i]
        vp = float(v_prev)
        if vp == 0.0:
            continue  # index pauses, never ÷0
        step_flow = sum(a for d, a in acct_flow.items() if d_prev < d <= d_cur)
        r = (float(v_cur) - step_flow) / vp
        if r <= 0.0:
            continue  # value wiped / nonsensical step -> pause
        factor *= r
        used = True

    if not used:
        return None
    span = (daily_values[-1][0] - daily_values[0][0]).days
    if span <= 0:
        return None
    return factor ** (_DAYS_PER_YEAR / span) - 1.0
