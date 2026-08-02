"""Ruling 005.3 edge tests + 005.4 aborted-pair risk replay + 005.7 oracles.

Oracle expected values are hand-calculated literals; derivations in comments.
"""
from decimal import Decimal

from conftest import T0, ScriptedCaller, long_decision
from engine import state, execution, replay, decisions, features, rounds, marketdata

MIN = 60


def mk(t, o, h, l, c, v=1):
    return {"t": t, "o": Decimal(str(o)), "h": Decimal(str(h)),
            "l": Decimal(str(l)), "c": Decimal(str(c)), "v": Decimal(str(v))}


def open_pos(a, pos, size, price, stop=None, tp=None, inv=None, t=0):
    d = {"position": pos, "size_usd": size, "stop_loss": stop, "take_profit": tp,
         "thesis": "t", "watch_condition": None,
         "invalidation": inv or {"timeframe": "1m_intrabar",
                                 "operator": "price_at_or_below" if pos == "long"
                                 else "price_at_or_above",
                                 "level": price * 0.5 if pos == "long" else price * 2}}
    execution.apply_decision(a, d, Decimal(str(price)), t)
    return a


# ---- 005.3 dedicated edge tests ----

def test_short_gap_through_take_profit_fills_at_open():
    a = state.new_account("BTC", "haiku", "raw")
    open_pos(a, "short", 5000, 100, tp=90)
    rec = []
    replay.replay([a], [mk(60, 85, 86, 84, 85)], rec)   # gaps DOWN through tp=90
    assert a["trades"][0]["reason"] == "take_profit"
    assert a["trades"][0]["exit"] == "85"               # open, better-for-noone rule: worse fill
    assert rec[0]["gap"] is True


def test_post_trigger_action_increased():
    a = state.new_account("BTC", "haiku", "raw")
    open_pos(a, "long", 5000, 100,
             inv={"timeframe": "1m_intrabar", "operator": "price_at_or_below", "level": 99})
    rec = []
    replay.replay([a], [mk(60, 100, 100, 98, 99)], rec)   # latches, no exit
    lc = a["lifecycle"]
    assert lc["triggered"] is not None and a["qty"] != 0
    inc = {"position": "long", "size_usd": 8000, "stop_loss": None, "take_profit": None,
           "thesis": "doubling down", "invalidation": None, "watch_condition": None}
    assert decisions.validate(a, inc, Decimal("99")) == []
    snap = {"P_T": Decimal("99"), "T": 3600}
    rounds._commit_account(a, inc, snap, 3600)
    assert lc["post_trigger_action"] == "increased"


# ---- 005.4 aborted-pair risk replay ----

def test_aborted_pair_still_gets_market_driven_exits_and_latches(accounts, snapshots, cfg):
    p = snapshots["BTC"]["P_T"]
    raw, ta = accounts["btc_haiku_raw"], accounts["btc_haiku_ta"]
    open_pos(raw, "long", 4000, float(p), stop=float(p * Decimal("0.999")),
             t=T0 - 3600)
    open_pos(ta, "long", 4000, float(p),
             inv={"timeframe": "1m_intrabar", "operator": "price_at_or_below",
                  "level": float(p * Decimal("0.9995"))}, t=T0 - 3600)
    bad = dict(long_decision(p), size_usd=-1)
    caller = ScriptedCaller({"btc_haiku_raw": [bad, bad]})
    ledger, archive, _ = rounds.run_boundary(T0, snapshots, accounts, caller, cfg)
    ab = [e for e in ledger if e["pair"] == "btc_haiku"][0]
    assert ab["status"] == "PAIR_ABORTED"
    assert raw["qty"] != 0 and ta["qty"] != 0          # no decision-generated change
    # post-T replay proceeds for BOTH twins despite the abort
    down = [mk(T0 + i * 60, float(p) * 0.998, float(p) * 0.9985,
               float(p) * 0.997, float(p) * 0.998) for i in range(3)]
    rec = []
    rounds.post_boundary_replay(accounts, "BTC", down, rec)
    assert raw["trades"] and raw["trades"][-1]["reason"] == "stop_loss"   # stop exit
    assert ta["lifecycle"]["triggered"] is not None                        # latch


# ---- 005.7 independent calculation oracles (hand-computed literals) ----

def test_oracle_sma():
    # closes 1..25: SMA20 = mean(6..25) = 15.5 ; SMA50 insufficient -> n/a
    cs = [Decimal(i) for i in range(1, 26)]
    assert features.sma(cs, 20, 2) == "15.50"
    assert features.sma(cs, 50, 2) == "n/a"


def test_oracle_rsi():
    # 15 closes: +1 x7 then -1 x7 alternating? Use: up 10,11,...,17 (7 gains of 1),
    # then 16,15,...,10 (7 losses of 1). avg gain = 7/14 = 0.5, avg loss = 0.5
    # RS=1 -> RSI = 100 - 100/2 = 50.0
    cs = [Decimal(x) for x in [10, 11, 12, 13, 14, 15, 16, 17, 16, 15, 14, 13, 12, 11, 10]]
    assert features.rsi14(cs) == "50.0"


def test_oracle_atr():
    # 15 candles: first sets prev close 100; then 14 candles h=102 l=98 c=100
    # TR = max(4, |102-100|, |98-100|) = 4 each; ATR = 4.00
    cands = [mk(0, 100, 100, 100, 100)] + [mk(i * 3600, 100, 102, 98, 100)
                                           for i in range(1, 15)]
    assert features.atr14(cands, 2) == "4.00"


def test_oracle_volume_and_ratio():
    # 24 baseline candles v=4, latest v=6: mean=4.0, ratio=1.50
    cands = [mk(i * 3600, 1, 1, 1, 1, 4) for i in range(24)] + [mk(24 * 3600, 1, 1, 1, 1, 6)]
    cur, base, ratio = features.volume_features(cands, 1)
    assert (cur, base, ratio) == ("6.0", "4.0", "1.50")


def test_oracle_vwap_and_pct():
    # 24 candles: 12 at typical 90 (v=1), 12 at typical 110 (v=3)
    # VWAP = (12*90*1 + 12*110*3)/(12*1+12*3) = (1080+3960)/48 = 105.00
    # P_T=110 -> pct = (110-105)/105*100 = +4.76
    cands = ([mk(i * 3600, 90, 90, 90, 90, 1) for i in range(12)]
             + [mk((12 + i) * 3600, 110, 110, 110, 110, 3) for i in range(12)])
    vwap, pct = features.vwap24(cands, Decimal("110"), 2)
    assert (vwap, pct) == ("105.00", "+4.76")


def test_oracle_liquidation_thresholds():
    # LONG: E=9975 (10000 - 25 fee on 50000 notional @ 0.0005), q=500, entry=100
    # L = (500*100 - 9975) / (0.98*500) = 40025/490 = 81.6836734693...
    a = state.new_account("BTC", "h", "raw")
    open_pos(a, "long", 50000, 100)
    assert str(state.liq_threshold(a))[:10] == "81.6836734"
    # SHORT: q=-500, entry=100, E=9975: L = (-50000+ -? ) formula:
    # (q*e - E)/((1.02)*q) = (-50000-9975)/(1.02*-500) = -59975/-510 = 117.5980392...
    b = state.new_account("BTC", "h", "raw")
    open_pos(b, "short", 50000, 100)
    assert str(state.liq_threshold(b))[:11] == "117.5980392"


def test_oracle_pnl_fees_weighted_entry():
    # open long 5000 @100: qty=50, open fee = 5000*0.0005 = 2.50 -> E=9997.50
    a = state.new_account("BTC", "h", "raw")
    open_pos(a, "long", 5000, 100)
    assert a["E"] == Decimal("9997.5000")
    # increase to 8000 @110: target qty = 8000/110 = 72.727272 (round down 6dp)
    # delta = 22.727272; weighted entry = (50*100 + 22.727272*110)/72.727272
    # = (5000+2499.99992)/72.727272 = 103.1249998...
    d = {"position": "long", "size_usd": 8000, "stop_loss": None, "take_profit": None,
         "thesis": "t", "invalidation": None, "watch_condition": None}
    execution.apply_decision(a, d, Decimal("110"), 60)
    assert str(a["entry"])[:9] == "103.12499"
    # reduce to 3000 @120: target qty=25, reduced=47.727272
    # pnl = 47.727272*(120-entry) ~= 47.727272*16.875 = 805.397...
    d2 = dict(d, size_usd=3000)
    execution.apply_decision(a, d2, Decimal("120"), 120)
    assert a["qty"] == Decimal("25.000000")
    # close @120: pnl = 25*(120-entry) = 25*16.87500 = 421.875..., fee=1.50
    d3 = {"position": "flat", "size_usd": 0, "stop_loss": None, "take_profit": None,
          "thesis": "t", "invalidation": None, "watch_condition": None}
    execution.apply_decision(a, d3, Decimal("120"), 180)
    assert a["qty"] == 0 and a["entry"] is None
    tr = a["trades"][-1]
    assert tr["reason"] == "decision_close"
    # fee on close = 25*120*0.0005 = 1.50
    assert Decimal(tr["fee"]) == Decimal("1.5000")
    # independent step-by-step Decimal reconciliation (spec-derived, engine-free)
    q1, e1 = Decimal("50"), Decimal("100")
    fee_open = Decimal("5000") * Decimal("0.0005")
    q2 = Decimal("72.727272")
    dq = q2 - q1
    fee_inc = dq * Decimal("110") * Decimal("0.0005")
    e2 = (q1 * e1 + dq * Decimal("110")) / q2
    q3 = Decimal("25")
    red = q2 - q3
    pnl_red = red * (Decimal("120") - e2)
    fee_red = red * Decimal("120") * Decimal("0.0005")
    pnl_cls = q3 * (Decimal("120") - e2)
    fee_cls = q3 * Decimal("120") * Decimal("0.0005")
    expected = (Decimal("10000.00") - fee_open - fee_inc
                + pnl_red - fee_red + pnl_cls - fee_cls)
    assert a["E"] == expected
