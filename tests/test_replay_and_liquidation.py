"""Event-ordering contract, gap fills, liquidation math, invalidation latching."""
from decimal import Decimal

from engine import state, execution, replay, lifecycle

MIN = 60


def mk(t, o, h, l, c, v=1):
    return {"t": t, "o": Decimal(str(o)), "h": Decimal(str(h)),
            "l": Decimal(str(l)), "c": Decimal(str(c)), "v": Decimal(str(v))}


def long_acct(size=5000, price=100, stop=None, tp=None, lev_inv=None, start_t=0):
    a = state.new_account("BTC", "haiku", "raw")
    dec = {"position": "long", "size_usd": size, "stop_loss": stop,
           "take_profit": tp, "thesis": "t",
           "invalidation": lev_inv or {"timeframe": "1m_intrabar",
                                       "operator": "price_at_or_below", "level": 90},
           "watch_condition": None}
    execution.apply_decision(a, dec, Decimal(str(price)), start_t)
    return a


def test_gap_through_stop_fills_at_open():
    a = long_acct(stop=95)
    rec = []
    replay.replay([a], [mk(60, 92, 93, 91, 92)], rec)
    assert a["trades"][0]["reason"] == "stop_loss"
    assert a["trades"][0]["exit"] == "92"          # worse (gapped) open, not 95
    assert rec[0]["gap"] is True


def test_gap_through_tp_fills_at_open():
    a = long_acct(tp=105)
    rec = []
    replay.replay([a], [mk(60, 108, 109, 107, 108)], rec)
    assert a["trades"][0]["reason"] == "take_profit"
    assert a["trades"][0]["exit"] == "108"


def test_intracandle_stop_fills_at_level():
    a = long_acct(stop=95)
    rec = []
    replay.replay([a], [mk(60, 100, 101, 94, 96)], rec)
    assert a["trades"][0]["exit"] == "95"


def test_protective_first_when_ambiguous():
    a = long_acct(stop=95, tp=105)
    rec = []
    replay.replay([a], [mk(60, 100, 106, 94, 100)], rec)   # both reachable
    assert a["trades"][0]["reason"] == "stop_loss"
    assert any(r["e"] == "AMBIGUOUS_CANDLE_PROTECTIVE_FIRST" for r in rec)


def test_protective_level_is_higher_of_stop_and_liq_for_long():
    a = long_acct(size=45000, price=100, stop=78)   # 4.5x lev; liq ~= 79.42 > stop
    liq = state.liq_threshold(a)
    assert liq > Decimal("78")
    rec = []
    replay.replay([a], [mk(60, 100, 100, 77, 85)], rec)
    assert a["trades"][0]["reason"] == "liquidation"   # higher protective level wins
    assert Decimal(a["trades"][0]["exit"]) == liq


def test_liquidation_formulas_and_residual_equity():
    a = long_acct(size=40000, price=100)            # q=400, E≈9980
    q, E = a["qty"], a["E"]
    L = (q * Decimal("100") - E) / ((1 - state.MAINT_MARGIN) * q)
    assert state.liq_threshold(a) == L
    rec = []
    replay.replay([a], [mk(60, 100, 100, float(L) - 1, float(L))], rec)
    assert a["trades"][0]["reason"] == "liquidation"
    assert a["terminal"] is False                    # residual equity survives
    assert a["E"] > 0


def test_short_liquidation_and_terminal_zero():
    a = state.new_account("BTC", "haiku", "raw")
    dec = {"position": "short", "size_usd": 50000, "stop_loss": None,
           "take_profit": None, "thesis": "t",
           "invalidation": {"timeframe": "1m_intrabar",
                            "operator": "price_at_or_above", "level": 130},
           "watch_condition": None}
    execution.apply_decision(a, dec, Decimal("100"), 0)
    L = state.liq_threshold(a)
    assert L > Decimal("100")
    rec = []
    # gap far beyond liquidation so post-fee equity goes to zero
    replay.replay([a], [mk(60, 125, 130, 124, 129)], rec)
    assert a["trades"][0]["reason"] == "liquidation"
    assert a["terminal"] is True and a["E"] == 0


def test_liquidation_charges_fee():
    a = long_acct(size=40000, price=100)
    rec = []
    L = state.liq_threshold(a)
    replay.replay([a], [mk(60, 100, 100, float(L) - 1, float(L))], rec)
    assert Decimal(a["trades"][0]["fee"]) > 0


def test_intrabar_invalidation_latch_and_same_candle_exit():
    a = long_acct(stop=95, lev_inv={"timeframe": "1m_intrabar",
                                    "operator": "price_at_or_below", "level": 96})
    lc = a["lifecycle"]
    rec = []
    replay.replay([a], [mk(60, 100, 100, 94, 95)], rec)
    assert lc["triggered"] is not None
    assert "SAME_CANDLE_INVALIDATION_AND_EXIT" in lc["records"]
    # latch is permanent: second trigger attempt does not overwrite
    first = dict(lc["triggered"])
    lifecycle.latch(lc, 999, 999)
    assert lc["triggered"] == first


def test_candle_before_lifecycle_start_cannot_trigger():
    """Ruling 004.3: first eligible candle has open >= T."""
    a = long_acct(stop=95, start_t=120)
    rec = []
    replay.replay([a], [mk(60, 90, 90, 90, 90)], rec)      # opens before T=120
    assert a["qty"] != 0 and a["lifecycle"]["triggered"] is None
    replay.replay([a], [mk(120, 90, 90, 90, 90)], rec)     # first eligible candle
    assert a["trades"] and a["trades"][0]["reason"] == "stop_loss"


def test_1h_close_invalidation_only_at_completed_close():
    a = long_acct(lev_inv={"timeframe": "1h_close",
                           "operator": "price_at_or_below", "level": 98}, start_t=0)
    rec = []
    mid = [mk(t, 97, 97, 96, 97) for t in range(0, 3540, 60)]    # intrahour below level
    replay.replay([a], mid, rec)
    assert a["lifecycle"]["triggered"] is None                    # not at a close yet
    replay.replay([a], [mk(3540, 97, 97, 96, 97)], rec)           # completes the hour
    assert a["lifecycle"]["triggered"] is not None
    assert a["lifecycle"]["triggered"]["t"] == 3600


def test_ended_lifecycle_never_triggers():
    a = long_acct(stop=99, lev_inv={"timeframe": "1h_close",
                                    "operator": "price_at_or_below", "level": 90})
    rec = []
    replay.replay([a], [mk(60, 98, 98, 97, 98)], rec)             # stopped out
    lc = a["lifecycle"]
    assert lc is not None and lc["ended_t"] == 60
    hour = [mk(t, 85, 85, 84, 85) for t in range(120, 3600, 60)]
    replay.replay([a], hour + [mk(3540, 85, 85, 84, 85)], rec)
    assert lc["triggered"] is None
