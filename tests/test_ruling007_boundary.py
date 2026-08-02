"""Ruling 007: exact $10 executable-delta boundary, long and short, Decimal."""
from decimal import Decimal

from engine import state, decisions, execution

P = Decimal("100.00")
LEV = "TARGET_EXCEEDS_MAX_LEVERAGE"


def mk_acct(pos, notional):
    a = state.new_account("BTC", "h", "raw")
    d = {"position": pos, "size_usd": float(notional), "stop_loss": None,
         "take_profit": None, "thesis": "t", "watch_condition": None,
         "invalidation": {"timeframe": "1m_intrabar",
                          "operator": "price_at_or_below" if pos == "long"
                          else "price_at_or_above",
                          "level": float(P) * (0.5 if pos == "long" else 2)}}
    execution.apply_decision(a, d, P, 0)
    assert abs(a["qty"]) * P == notional          # exact Decimal notional
    return a


def hold(pos, size):
    return {"position": pos, "size_usd": size, "stop_loss": None,
            "take_profit": None, "thesis": "t", "invalidation": None,
            "watch_condition": None}


def lev(rs):
    return any(LEV in x for x in rs)


def drift_above_5x(a, pos):
    a["entry"] = P * (Decimal("1.18") if pos == "long" else Decimal("0.82"))
    cur = abs(a["qty"]) * P
    assert cur > state.MAX_LEVERAGE * state.equity_at(a, P)
    return cur


def test_plus_9_99_no_execution_long_short():
    for pos in ("long", "short"):
        a = mk_acct(pos, Decimal("5000"))
        d = hold(pos, Decimal("5009.99"))
        assert decisions.validate(a, d, P) == []
        log = execution.apply_decision(a, d, P, 60)
        assert "NO_EXECUTION_BELOW_MINIMUM" in log
        assert abs(a["qty"]) * P == Decimal("5000")


def test_exactly_plus_10_is_executable_increase_long_short():
    for pos in ("long", "short"):
        a = mk_acct(pos, Decimal("5000"))
        d = hold(pos, Decimal("5010.00"))
        assert decisions.validate(a, d, P) == []      # within 5x: allowed
        log = execution.apply_decision(a, d, P, 60)
        assert "increased" in log
        assert abs(a["qty"]) * P == Decimal("5010.00")


def test_plus_10_01_is_executable_increase_long_short():
    for pos in ("long", "short"):
        a = mk_acct(pos, Decimal("5000"))
        d = hold(pos, Decimal("5010.01"))
        assert decisions.validate(a, d, P) == []
        log = execution.apply_decision(a, d, P, 60)
        assert "increased" in log


def test_minus_9_99_no_execution_long_short():
    for pos in ("long", "short"):
        a = mk_acct(pos, Decimal("5000"))
        d = hold(pos, Decimal("4990.01"))
        assert decisions.validate(a, d, P) == []
        log = execution.apply_decision(a, d, P, 60)
        assert "NO_EXECUTION_BELOW_MINIMUM" in log


def test_exactly_minus_10_is_executable_reduction_long_short():
    for pos in ("long", "short"):
        a = mk_acct(pos, Decimal("5000"))
        d = hold(pos, Decimal("4990.00"))
        assert decisions.validate(a, d, P) == []
        log = execution.apply_decision(a, d, P, 60)
        assert "reduced" in log
        assert abs(a["qty"]) * P == Decimal("4990.00")


def test_minus_10_01_is_executable_reduction_long_short():
    for pos in ("long", "short"):
        a = mk_acct(pos, Decimal("5000"))
        d = hold(pos, Decimal("4989.99"))
        assert decisions.validate(a, d, P) == []
        log = execution.apply_decision(a, d, P, 60)
        assert "reduced" in log


def test_exactly_plus_10_above_5x_rejected_long_short():
    for pos in ("long", "short"):
        a = mk_acct(pos, Decimal("45000"))
        cur = drift_above_5x(a, pos)
        assert lev(decisions.validate(a, hold(pos, cur + Decimal("10.00")), P))
        # and +9.99 stays a non-executable hold: allowed
        assert not lev(decisions.validate(a, hold(pos, cur + Decimal("9.99")), P))


def test_exactly_minus_10_above_5x_accepted_long_short():
    for pos in ("long", "short"):
        a = mk_acct(pos, Decimal("45000"))
        cur = drift_above_5x(a, pos)
        assert not lev(decisions.validate(a, hold(pos, cur - Decimal("10.00")), P))
