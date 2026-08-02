"""Targeted micro-tests for remaining uncovered branches."""
import os
from decimal import Decimal

from engine import state, execution, replay, lifecycle, persistence

MIN = 60


def mk(t, o, h, l, c):
    return {"t": t, "o": Decimal(str(o)), "h": Decimal(str(h)),
            "l": Decimal(str(l)), "c": Decimal(str(c)), "v": Decimal("1")}


def short_acct(size=5000, price=100, stop=None, tp=None):
    a = state.new_account("BTC", "h", "raw")
    d = {"position": "short", "size_usd": size, "stop_loss": stop,
         "take_profit": tp, "thesis": "t", "watch_condition": None,
         "invalidation": {"timeframe": "1m_intrabar",
                          "operator": "price_at_or_above", "level": 200}}
    execution.apply_decision(a, d, Decimal(str(price)), 0)
    return a


def test_short_intracandle_stop_and_tp():
    a = short_acct(stop=105)
    replay.replay([a], [mk(60, 100, 106, 99, 100)], [])
    assert a["trades"][0]["reason"] == "stop_loss" and a["trades"][0]["exit"] == "105"
    b = short_acct(tp=95)
    replay.replay([b], [mk(60, 100, 101, 94, 96)], [])
    assert b["trades"][0]["reason"] == "take_profit"


def test_short_gap_stop_at_open():
    a = short_acct(stop=105)
    replay.replay([a], [mk(60, 108, 109, 107, 108)], [])
    assert a["trades"][0]["exit"] == "108"


def test_flat_watch_condition_triggers_in_replay():
    a = state.new_account("BTC", "h", "raw")
    d = {"position": "flat", "size_usd": 0, "stop_loss": None, "take_profit": None,
         "thesis": "t", "invalidation": None,
         "watch_condition": {"timeframe": "1m_intrabar",
                             "operator": "price_at_or_above", "level": 110}}
    execution.apply_decision(a, d, Decimal("100"), 0)
    replay.replay([a], [mk(60, 111, 112, 110, 111)], [])
    assert a["watch"]["triggered"] is not None


def test_close_on_flat_is_noop_and_end_twice():
    a = state.new_account("BTC", "h", "raw")
    execution.close_position(a, Decimal("100"), "decision_close", 0)
    assert a["trades"] == []
    lc = lifecycle.new_lifecycle(0, {"timeframe": "1h_close",
                                     "operator": "price_at_or_below", "level": 1})
    lifecycle.end(lc, 10, "x")
    lifecycle.end(lc, 20, "y")
    assert lc["ended_t"] == 10 and lc["end_reason"] == "x"
    assert lifecycle.active_at(None, 0) is False
    assert lifecycle.active_at(lc, 5) is True and lifecycle.active_at(lc, 15) is False


def test_protective_none_when_flat():
    a = state.new_account("BTC", "h", "raw")
    assert replay._protective(a) == (None, None)
    assert state.liq_threshold(a) is None
    assert state.is_liquidatable(a, Decimal("1")) is False


def test_persistence_watch_heartbeat_ledger(tmp_path):
    a = state.new_account("BTC", "h", "raw")
    a["watch"] = {"timeframe": "1h_close", "operator": "price_at_or_above",
                  "level": Decimal("110"), "triggered": None}
    p = str(tmp_path / "s.json")
    persistence.save_state(p, {a["id"]: a}, {"m": 1})
    loaded, _ = persistence.load_state(p)
    assert loaded[a["id"]]["watch"]["level"] == Decimal("110")
    assert persistence.read_ledger(str(tmp_path / "missing.jsonl")) == []
    hb = str(tmp_path / "hb.json")
    persistence.write_heartbeat(hb, 123, "hash", "v1-BTC-x")
    assert os.path.exists(hb)
    led = [{"round_id": "r", "pair": "p", "status": "PAIR_COMMITTED"}]
    assert persistence.committed_round_ids(led) == {"r"}


def test_reversal_blocked_when_terminal_after_close():
    a = state.new_account("BTC", "h", "raw")
    d = {"position": "long", "size_usd": 49000, "stop_loss": None,
         "take_profit": None, "thesis": "t", "watch_condition": None,
         "invalidation": {"timeframe": "1m_intrabar",
                          "operator": "price_at_or_below", "level": 50}}
    execution.apply_decision(a, d, Decimal("100"), 0)
    a["entry"] = Decimal("200")           # force deep loss so flip close zeroes E
    rev = {"position": "short", "size_usd": 1000, "stop_loss": None,
           "take_profit": None, "thesis": "t", "watch_condition": None,
           "invalidation": {"timeframe": "1m_intrabar",
                            "operator": "price_at_or_above", "level": 150}}
    log = execution.apply_decision(a, rev, Decimal("100"), 60)
    assert log == ["reversal_close"] and a["terminal"] and a["qty"] == 0
