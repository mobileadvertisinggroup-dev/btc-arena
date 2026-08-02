"""Ruling 006: leverage semantics A-F, gap wording/fills, coordinator paths."""
from decimal import Decimal

from conftest import T0, ScriptedCaller, long_decision, run_prod
from engine import state, decisions, execution, replay, config, persistence, recovery

P = Decimal("100.00")
LEV = "TARGET_EXCEEDS_MAX_LEVERAGE"


def mk(t, o, h, l, c):
    return {"t": t, "o": Decimal(str(o)), "h": Decimal(str(h)),
            "l": Decimal(str(l)), "c": Decimal(str(c)), "v": Decimal("1")}


def acct(pos=None, size=5000, price=P, entry=None):
    a = state.new_account("BTC", "h", "raw")
    if pos:
        d = {"position": pos, "size_usd": size, "stop_loss": None,
             "take_profit": None, "thesis": "t", "watch_condition": None,
             "invalidation": {"timeframe": "1m_intrabar",
                              "operator": "price_at_or_below" if pos == "long"
                              else "price_at_or_above",
                              "level": float(price) * (0.5 if pos == "long" else 2)}}
        execution.apply_decision(a, d, price, 0)
        if entry is not None:
            a["entry"] = Decimal(str(entry))   # simulate mark-to-market drift
    return a


def hold(pos, size):
    return {"position": pos, "size_usd": size, "stop_loss": None,
            "take_profit": None, "thesis": "t", "invalidation": None,
            "watch_condition": None}


def lev(reasons):
    return any(LEV in x for x in reasons)


def test_A_open_from_flat_capped_long_and_short():
    for pos in ("long", "short"):
        d = long_decision(P, 60000)
        d["position"] = pos
        if pos == "short":
            d["stop_loss"], d["take_profit"] = 110.0, 90.0
            d["invalidation"] = {"timeframe": "1m_intrabar",
                                 "operator": "price_at_or_above", "level": 130}
        assert lev(decisions.validate(acct(), d, P))
        d2 = dict(d, size_usd=49000)
        assert not lev(decisions.validate(acct(), d2, P))


def test_B_reversal_capped():
    for pos, opp in (("long", "short"), ("short", "long")):
        a = acct(pos, 5000)
        rev = hold(opp, 60000)
        rev["invalidation"] = {"timeframe": "1m_intrabar",
                               "operator": "price_at_or_above" if opp == "short"
                               else "price_at_or_below",
                               "level": float(P) * (2 if opp == "short" else 0.5)}
        assert lev(decisions.validate(a, rev, P))


def test_C_same_side_increase_capped():
    for pos in ("long", "short"):
        a = acct(pos, 5000)
        assert lev(decisions.validate(a, hold(pos, 60000), P))
        assert not lev(decisions.validate(a, hold(pos, 20000), P))


def test_D_exact_hold_allowed_above_5x():
    for pos, sign in (("long", 1), ("short", -1)):
        a = acct(pos, 45000)
        # drift: entry moved against so equity collapses; effective lev > 5x
        a["entry"] = P * (Decimal("1.18") if pos == "long" else Decimal("0.82"))
        eq = state.equity_at(a, P)
        cur = abs(a["qty"]) * P
        assert cur > state.MAX_LEVERAGE * eq          # above 5x effective
        assert not lev(decisions.validate(a, hold(pos, float(cur)), P))


def test_E_reduction_allowed_even_if_still_above_5x():
    for pos in ("long", "short"):
        a = acct(pos, 45000)
        a["entry"] = P * (Decimal("1.18") if pos == "long" else Decimal("0.82"))
        cur = abs(a["qty"]) * P
        target = float(cur - Decimal("5000"))         # reduce, still above 5x eq
        eq = state.equity_at(a, P)
        assert Decimal(str(target)) > state.MAX_LEVERAGE * eq
        assert not lev(decisions.validate(a, hold(pos, target), P))


def test_F_above_5x_may_hold_or_reduce_but_not_increase():
    for pos in ("long", "short"):
        a = acct(pos, 45000)
        a["entry"] = P * (Decimal("1.18") if pos == "long" else Decimal("0.82"))
        cur = abs(a["qty"]) * P
        assert lev(decisions.validate(a, hold(pos, float(cur + 5000)), P))
        assert not lev(decisions.validate(a, hold(pos, float(cur)), P))
        assert not lev(decisions.validate(a, hold(pos, float(cur / 2)), P))


def test_gap_through_liquidation_long_and_short():
    a = acct("long", 45000)
    L = state.liq_threshold(a)
    replay.replay([a], [mk(60, float(L) * 0.9, float(L) * 0.9,
                           float(L) * 0.89, float(L) * 0.9)], [])
    assert a["trades"][0]["reason"] == "liquidation"
    assert Decimal(a["trades"][0]["exit"]) < L        # filled at worse open
    b = acct("short", 45000)
    Ls = state.liq_threshold(b)
    replay.replay([b], [mk(60, float(Ls) * 1.1, float(Ls) * 1.11,
                           float(Ls) * 1.09, float(Ls) * 1.1)], [])
    assert b["trades"][0]["reason"] == "liquidation"
    assert Decimal(b["trades"][0]["exit"]) > Ls


def test_gap_wording_in_system_prompt_and_leverage_wording():
    s = config.load_text("prompts/v1/system.txt")
    assert ("If a one-minute candle opens beyond a resting execution level, the "
            "fill occurs at that candle's opening price. For a protective stop or "
            "liquidation this may be worse than the trigger level; for a "
            "take-profit it may be better than the target level.") in s
    assert "worse (gapped) price" not in s
    assert "NEW or INCREASED exposure" in s
    assert "never force-deleveraged" in s


def test_coordinator_coin_termination_on_replay_gap(accounts, snapshots, cfg):
    from conftest import load_fix
    from engine import marketdata
    good = [c for c in marketdata.to_dec(load_fix("SOL", "1m"))
            if T0 <= c["t"] < T0 + 600]
    gapped = good[:3] + good[5:]                      # missing candles
    import tempfile
    store = tempfile.mkdtemp(prefix="arena-term-")
    led1, *_ = run_prod(accounts, snapshots, cfg, None, store=store,
                        candles={"SOL": gapped})
    term = [e for e in led1 if "replay" in e and
            e["replay"][0]["e"] == "COIN_TERMINATED"]
    assert term and term[0]["round_id"].startswith("v1-SOL-")
    # next boundary on same store: SOL pairs abort with COIN_TERMINATED
    accounts2, meta = persistence.load_state(store + "/state.json")
    assert meta["coin_terminated"]["SOL"] is True
    led2, _, _ = recovery.run_checkpointed(T0 + 3600, snapshots,
                                           ScriptedCaller({}), cfg, store)
    sol = [e for e in led2 if e.get("pair", "").startswith("sol_")]
    assert sol and all(e["reason"] == "COIN_TERMINATED" for e in sol)
    other = [e for e in led2 if e.get("pair", "").startswith(("btc_", "eth_"))]
    assert all(e["status"] != "PAIR_ABORTED" or e["reason"] != "COIN_TERMINATED"
               for e in other)


def test_coordinator_new_boundary_reset(accounts, snapshots, cfg):
    import tempfile
    store = tempfile.mkdtemp(prefix="arena-reset-")
    run_prod(accounts, snapshots, cfg, None, store=store)
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["boundary"] == T0 and meta["boundary_complete"]
    led2, _, _ = recovery.run_checkpointed(T0 + 3600, snapshots,
                                           ScriptedCaller({}), cfg, store)
    _, meta2 = persistence.load_state(store + "/state.json")
    assert meta2["boundary"] == T0 + 3600             # reset for new boundary
    assert len(led2) == 9
