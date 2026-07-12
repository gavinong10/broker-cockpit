"""Known-answer tests for the flow-adjusted performance engine.

All pure (no DB, no network). Sign convention under test is the module's
investor convention: deposits negative, withdrawals positive, terminal value a
positive inflow. These are the numbers the owner judges their trading by, so the
cases are hand-computed and asserted tightly.
"""
from datetime import date
from decimal import Decimal

from app.performance import (
    carry_forward_values,
    dollar_pnl,
    money_weighted_return,
    net_contributions,
    net_contributions_series,
    opening_value,
    twr,
)

D = Decimal


# --- money_weighted_return (XIRR) ---------------------------------------------

def test_mwr_single_year_ten_percent():
    # deposit 100 at t0, worth 110 exactly one year later -> 10.00%/yr.
    r = money_weighted_return([(date(2025, 1, 1), D("-100"))], D("110"), date(2026, 1, 1))
    assert r is not None
    assert round(r, 6) == 0.100000


def test_mwr_hand_computed_mid_period_withdrawal():
    # Integer-year exponents (Actual/365) so the check needs no transcendentals:
    #   -1000 at t0, +600 withdrawal at 1yr, +720 terminal at 2yr.
    #   NPV = -1000 + 600/(1+r) + 720/(1+r)^2.
    #   At r=0.20: 600/1.2 = 500 and 720/1.44 = 500, sum = 1000 -> NPV = 0 EXACTLY.
    # (2025->2026 and 2026->2027 are 365 days each; no leap year in the span.)
    flows = [(date(2025, 1, 1), D("-1000")), (date(2026, 1, 1), D("600"))]
    r = money_weighted_return(flows, D("720"), date(2027, 1, 1))
    assert r is not None
    assert abs(r - 0.20) < 1e-7


def test_mwr_no_flows_is_none():
    # Only a terminal inflow (one sign) -> undefined, must not divide by zero.
    assert money_weighted_return([], D("100"), date(2026, 1, 1)) is None


def test_mwr_all_same_day_is_none():
    # Deposit and terminal on the same day -> zero span, undefined annualization.
    r = money_weighted_return([(date(2026, 1, 1), D("-100"))], D("110"), date(2026, 1, 1))
    assert r is None


def test_mwr_only_deposits_is_none():
    # Terminal value below zero-ish and every flow same sign as terminal.
    r = money_weighted_return([(date(2025, 1, 1), D("-100"))], D("0"), date(2026, 1, 1))
    # -100 (out) and 0 terminal -> only one strict sign -> None.
    assert r is None


def test_mwr_loss_is_negative():
    # deposit 100, worth 50 a year later -> -50%/yr.
    r = money_weighted_return([(date(2025, 1, 1), D("-100"))], D("50"), date(2026, 1, 1))
    assert r is not None
    assert round(r, 4) == -0.5000


# --- dollar_pnl + net contributions (exact) -----------------------------------

def test_dollar_pnl_simple():
    # deposit 100, value 110 -> +10 P&L.
    assert dollar_pnl([(date(2025, 1, 1), D("-100"))], D("110")) == D("10")


def test_dollar_pnl_with_withdrawal():
    # deposit 1000, later withdraw 300 (came back to pocket), value now 900.
    # true P&L = 900 + 300 - 1000 = 200.
    flows = [(date(2025, 1, 1), D("-1000")), (date(2025, 6, 1), D("300"))]
    assert dollar_pnl(flows, D("900")) == D("200")


def test_dollar_pnl_anchor_shape():
    # Mirrors the live anchors: net deposits +116.5k, value 375.5k -> ~+259k.
    flows = [(date(2025, 1, 27), D("-27000")),
             (date(2025, 11, 13), D("-280000")),
             (date(2026, 1, 1), D("190500"))]  # withdrawals returned
    # net contributions = 27000 + 280000 - 190500 = 116500
    assert net_contributions(flows) == D("116500")
    assert dollar_pnl(flows, D("375500")) == D("259000")


def test_net_contributions_series_steps_up_and_down():
    flows = [(date(2025, 1, 1), D("-1000")),   # deposit -> +1000
             (date(2025, 6, 1), D("300"))]      # withdrawal -> -300
    series = net_contributions_series(flows)
    assert series == [(date(2025, 1, 1), D("1000")),
                      (date(2025, 6, 1), D("700"))]


def test_net_contributions_series_same_day_merges():
    flows = [(date(2025, 1, 1), D("-1000")), (date(2025, 1, 1), D("-500"))]
    series = net_contributions_series(flows)
    assert series == [(date(2025, 1, 1), D("1500"))]


# --- twr (estimated; secondary) -----------------------------------------------

def test_twr_flow_adjustment_zeroes_out():
    # 100 -> (doubles to 200, +100 deposit) 300 -> (halves) 150 over 365 days.
    #   step1 r = (300 - 100)/100 = 2.0   (deposit removed before ratio)
    #   step2 r = (150 - 0)/300   = 0.5
    #   total factor 1.0 -> 0% return, annualized 0%.
    daily = [(date(2025, 1, 1), D("100")),
             (date(2025, 6, 1), D("300")),
             (date(2026, 1, 1), D("150"))]
    flows = [(date(2025, 6, 1), D("-100"))]  # investor: deposit negative
    r = twr(daily, flows)
    assert r is not None
    assert abs(r - 0.0) < 1e-9


def test_twr_annualizes_full_year_double():
    # 100 -> 200 over exactly one year, no flows -> +100%/yr.
    daily = [(date(2025, 1, 1), D("100")), (date(2026, 1, 1), D("200"))]
    r = twr(daily, [])
    assert r is not None
    assert abs(r - 1.0) < 1e-9


def test_twr_pauses_on_zero_prev_value():
    # A zero mid value must not divide by zero. step1 (100->0) gives r=0 -> paused
    # (value wiped); step2 has V_prev=0 -> paused. Both paused -> safe None, no
    # ZeroDivisionError. The guard is what matters.
    daily = [(date(2025, 1, 1), D("100")),
             (date(2025, 6, 1), D("0")),
             (date(2026, 1, 1), D("120"))]
    assert twr(daily, []) is None


def test_twr_skips_only_the_zero_prev_step():
    # A legitimate run with one interior zero-prev step: the good steps still chain.
    # 100 ->(no flow) 150 [r=1.5], 150 ->(no flow) 300 [r=2.0] over 365 days.
    daily = [(date(2025, 1, 1), D("100")),
             (date(2025, 6, 1), D("150")),
             (date(2026, 1, 1), D("300"))]
    r = twr(daily, [])
    assert r is not None
    # factor 1.5*2.0 = 3.0 over one year -> +200%/yr.
    assert abs(r - 2.0) < 1e-9


def test_twr_too_short_is_none():
    assert twr([(date(2025, 1, 1), D("100"))], []) is None
    assert twr([], []) is None


# --- non-trading-day carry-forward (read-path helpers) ------------------------

def test_carry_forward_fills_interior_zeros():
    # Weekend/holiday rows stored as 0 must carry the prior positive value fwd,
    # not dip to zero. Mirrors 2025-12-31 (real) -> 2026-01-01..04 (holidays).
    series = [(date(2025, 12, 31), D("301979.47")),
              (date(2026, 1, 1), D("0")),
              (date(2026, 1, 2), D("320033.02")),
              (date(2026, 1, 3), D("0")),
              (date(2026, 1, 4), D("0"))]
    filled = carry_forward_values(series)
    assert filled == [(date(2025, 12, 31), D("301979.47")),
                      (date(2026, 1, 1), D("301979.47")),
                      (date(2026, 1, 2), D("320033.02")),
                      (date(2026, 1, 3), D("320033.02")),
                      (date(2026, 1, 4), D("320033.02"))]
    # No interior (or any) point is <= 0 after the fill.
    assert all(v > 0 for _, v in filled)


def test_carry_forward_drops_leading_nonpositive():
    # Nothing to carry before the first positive value -> those points drop.
    series = [(date(2025, 1, 1), D("0")),
              (date(2025, 1, 2), D("0")),
              (date(2025, 1, 3), D("100")),
              (date(2025, 1, 4), D("0"))]
    filled = carry_forward_values(series)
    assert filled == [(date(2025, 1, 3), D("100")),
                      (date(2025, 1, 4), D("100"))]


def test_opening_value_skips_zero_on_boundary_date():
    # YTD-style: the exact boundary date (Jan 1) is a holiday stored as 0, so the
    # opening value must resolve to the prior positive row (Dec 31), not $0.
    series = [(date(2025, 12, 30), D("300000")),
              (date(2025, 12, 31), D("301979.47")),
              (date(2026, 1, 1), D("0"))]
    assert opening_value(series, date(2026, 1, 1)) == (date(2025, 12, 31), D("301979.47"))


def test_opening_value_skips_run_of_zeros_before_boundary():
    series = [(date(2025, 12, 31), D("301979.47")),
              (date(2026, 1, 1), D("0")),
              (date(2026, 1, 3), D("0")),
              (date(2026, 1, 4), D("0"))]
    # Boundary on Jan 4 (a holiday) still carries back to Dec 31.
    assert opening_value(series, date(2026, 1, 4)) == (date(2025, 12, 31), D("301979.47"))


def test_opening_value_none_when_no_prior_positive():
    series = [(date(2026, 1, 1), D("0")), (date(2026, 1, 2), D("500"))]
    # Boundary before any positive value -> unavailable (never a $0 opening).
    assert opening_value(series, date(2026, 1, 1)) is None
