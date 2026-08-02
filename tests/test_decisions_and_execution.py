"""Cross-field validation strings, Decimal execution, fees, min-delta, leverage."""
from decimal import Decimal

from conftest import flat_decision, long_decision
from engine import state, decisions, execution

P = Decimal("100.00")


def acct(coin="BTC"):
    return state.new_account(coin, "haiku", "raw")


def open_long(a, size=5000, price=P):
    dec = long_decision(price, size)
    assert decisions.validate(a, dec, price) == []
    execution.apply_decision(a, dec, price, 0)
    return dec


def test_valid_open_passes_and_charges_fee():
    a = acct()
    open_long(a)
    assert a["qty"] == Decimal("50.000000")
    fee = Decimal("5000") * state.FEE_RATE
    assert a["E"] == Decimal("10000.00") - fee
    assert a["fees_total"] == fee


def test_flat_requires_zero_size():
    d = flat_decision(); d["size_usd"] = 5
    assert "size_usd must be 0 when position is flat" in decisions.validate(acct(), d, P)


def test_min_open_size_and_negative_and_nan():
    d = long_decision(P, 5)
    assert "size_usd must be at least 10 when opening or holding a position" \
        in decisions.validate(acct(), d, P)
    d2 = long_decision(P); d2["size_usd"] = -1
    assert "size_usd must be a finite, non-negative number" in decisions.validate(acct(), d2, P)
    d3 = long_decision(P); d3["size_usd"] = float("nan")
    assert "size_usd must be a finite, non-negative number" in decisions.validate(acct(), d3, P)


def test_leverage_rejected_not_clamped():
    d = long_decision(P, 60000)
    r = decisions.validate(acct(), d, P)
    assert any("TARGET_EXCEEDS_MAX_LEVERAGE" in x for x in r)
    a = acct()   # and nothing executed anywhere
    assert a["qty"] == 0


def test_invalidation_lifecycle_rules():
    a = acct()
    d = long_decision(P); d["invalidation"] = None
    assert "an invalidation is required when opening or reversing a position" \
        in decisions.validate(a, d, P)
    open_long(a)
    d2 = long_decision(P)          # holding: must NOT resubmit invalidation
    r = decisions.validate(a, d2, P)
    assert any("immutable invalidation" in x for x in r)
    d3 = flat_decision(); d3["invalidation"] = {"timeframe": "1h_close",
                                                "operator": "price_at_or_below", "level": 90}
    assert "invalidation must be null when your decision leaves you flat" \
        in decisions.validate(acct(), d3, P)


def test_watch_condition_rules_and_stop_sides():
    d = long_decision(P)
    d["watch_condition"] = {"timeframe": "1h_close", "operator": "price_at_or_above", "level": 120}
    assert "watch_condition must be null when your decision leaves you holding a position" \
        in decisions.validate(acct(), d, P)
    d2 = long_decision(P); d2["stop_loss"] = 120
    assert "stop_loss must be below the execution price for a long position" \
        in decisions.validate(acct(), d2, P)
    d3 = long_decision(P); d3["take_profit"] = 90
    assert "take_profit must be above the execution price for a long position" \
        in decisions.validate(acct(), d3, P)
    a = acct(); open_long(a)
    short = {"position": "short", "size_usd": 1000, "stop_loss": 90, "take_profit": 110,
             "thesis": "x", "invalidation": {"timeframe": "1h_close",
                                             "operator": "price_at_or_above", "level": 110},
             "watch_condition": None}
    r = decisions.validate(a, short, P)
    assert "stop_loss must be above the execution price for a short position" in r
    assert "take_profit must be below the execution price for a short position" in r


def test_condition_level_range():
    d = long_decision(P)
    d["invalidation"]["level"] = 1
    assert any("outside the accepted range" in x for x in decisions.validate(acct(), d, P))


def test_min_delta_no_execution():
    a = acct(); open_long(a, 5000)
    d = long_decision(P, 5005); d["invalidation"] = None
    log = execution.apply_decision(a, d, P, 60)
    assert "NO_EXECUTION_BELOW_MINIMUM" in log
    assert a["qty"] == Decimal("50.000000")


def test_exact_increase_reduce_and_fees():
    a = acct(); open_long(a, 5000)
    e0, f0 = a["E"], a["fees_total"]
    d = long_decision(P, 7000); d["invalidation"] = None
    assert "increased" in execution.apply_decision(a, d, P, 60)
    assert a["qty"] == Decimal("70.000000")
    assert a["fees_total"] - f0 == Decimal("2000") * state.FEE_RATE
    d2 = long_decision(P, 3000); d2["invalidation"] = None
    assert "reduced" in execution.apply_decision(a, d2, P, 120)
    assert a["qty"] == Decimal("30.000000")


def test_reversal_two_fees_new_lifecycle():
    a = acct(); open_long(a, 5000)
    lc1 = a["lifecycle"]
    f0 = a["fees_total"]
    rev = {"position": "short", "size_usd": 2000, "stop_loss": None, "take_profit": None,
           "thesis": "flip", "invalidation": {"timeframe": "1m_intrabar",
                                              "operator": "price_at_or_above", "level": 110},
           "watch_condition": None}
    assert decisions.validate(a, rev, P) == []
    log = execution.apply_decision(a, rev, P, 3600)
    assert log == ["reversal_close", "reversal_open"]
    assert a["qty"] == Decimal("-20.000000")
    assert a["fees_total"] - f0 == Decimal("5000") * state.FEE_RATE + Decimal("2000") * state.FEE_RATE
    assert a["lifecycle"] is not lc1 and lc1["ended_t"] == 3600
    assert a["lifecycle"]["start_t"] == 3600     # lifecycle starts exactly at T


def test_null_removes_resting_levels():
    a = acct(); open_long(a, 5000)
    assert a["stop"] is not None and a["tp"] is not None
    d = long_decision(P, 5000); d["invalidation"] = None
    d["stop_loss"] = None; d["take_profit"] = None
    execution.apply_decision(a, d, P, 60)
    assert a["stop"] is None and a["tp"] is None


def test_quantity_rounds_down():
    a = acct("SOL")   # 3 decimals
    dec = long_decision(Decimal("150.00"), 1000)
    execution.apply_decision(a, dec, Decimal("150.00"), 0)
    assert a["qty"] == Decimal("6.666")          # 6.6666.. rounded DOWN


def test_decision_close_charges_fee_and_records_trade():
    a = acct(); open_long(a, 5000)
    d = flat_decision()
    execution.apply_decision(a, d, Decimal("102.00"), 60)
    assert a["qty"] == 0 and len(a["trades"]) == 1
    tr = a["trades"][0]
    assert tr["reason"] == "decision_close" and Decimal(tr["fee"]) > 0
