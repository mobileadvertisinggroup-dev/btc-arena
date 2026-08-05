"""Mentor Ruling 021 remediation regressions: the authoritative one-minute
mark requires EXACTLY ONE strictly validated T-60 candle — an ambiguous
duplicate can never be selected for the mark, equity history, dashboard P&L
or final marked equity. One shared helper governs build_snapshot() and
_adopt_1m_marks(). All offline and deterministic."""
import os
from decimal import Decimal

import pytest

from conftest import T0, ScriptedCaller, long_decision
from engine import config, marketdata, persistence, recovery, state
from test_ruling011 import _sol_price
from test_ruling016 import mk_candles, snapshots_at, _prod_store
from test_ruling018 import _open_sol

HOUR = 3600
T1 = T0 + HOUR


def _dup_t60(T, price, close_a=100, close_b=200):
    """Complete flat hour whose T-60 minute has TWO individually valid but
    conflicting candles (the mentor's exact reproduction)."""
    candles = mk_candles(T - HOUR, 60, price)
    assert candles[-1]["t"] == T - 60
    a = dict(candles[-1], o=Decimal(str(close_a)), h=Decimal(str(close_a)),
             l=Decimal(str(close_a)), c=Decimal(str(close_a)))
    b = dict(candles[-1], o=Decimal(str(close_b)), h=Decimal(str(close_b)),
             l=Decimal(str(close_b)), c=Decimal(str(close_b)))
    return candles[:-1] + [a, b]


def test_shared_helper_requires_exactly_one_t60_candle():
    p = float(_sol_price())
    good = mk_candles(T1 - HOUR, 60, p)
    assert marketdata.authoritative_1m_mark(good, T1) == good[-1]["c"]
    with pytest.raises(marketdata.DataUnavailable):     # zero
        marketdata.authoritative_1m_mark(good[:-1], T1)
    with pytest.raises(marketdata.DataUnavailable):     # multiple
        marketdata.authoritative_1m_mark(_dup_t60(T1, p), T1)
    # build_snapshot and the coordinator share THIS helper (no divergence)
    for rel in ("engine/marketdata.py", "engine/recovery.py"):
        assert "authoritative_1m_mark" in open(
            os.path.join(config.ROOT, rel)).read()
    rsrc = open(os.path.join(config.ROOT, "engine", "recovery.py")).read()
    adopt = rsrc[rsrc.index("def _adopt_1m_marks"):
                 rsrc.index("if pre_replay_spec and")]
    assert 'c["t"] == T - 60' not in adopt          # no local selection rule


def test_first_boundary_duplicate_t60_no_mark_no_calls(cfg):
    """Required test 1: first official boundary, snapshot unavailable, two
    conflicting valid T-60 candles => mark None, zero model calls,
    DATA_UNAVAILABLE."""
    p = float(_sol_price())
    store = _prod_store()
    snaps = snapshots_at(T0)
    snaps["SOL"] = None
    caller = ScriptedCaller({})
    # first boundary: empty replay interval [T0, T0) but candles present
    spec = {"SOL": {"start": T0, "end": T0, "candles": _dup_t60(T0, p)}}
    ledger, _, _ = recovery.run_checkpointed(T0, snaps, caller, cfg, store,
                                             pre_replay_spec=spec)
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["marks"]["SOL"] is None              # no arbitrary pick
    assert not [c for c in caller.calls if c["id"].startswith("sol")]
    sol = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert sol["status"] == "PAIR_ABORTED"
    assert sol["reason"] == "DATA_UNAVAILABLE"
    # equity history for the coin is honestly null, never from close 100/200
    pt = meta["equity_history"]["sol_haiku_raw"][-1]
    assert pt["equity"] == "10000.00"                # flat: exact cash only


def test_crash_resume_shape_duplicate_t60_no_ambiguous_equity(cfg):
    """Required test 2: phase=pre_replay, replay_next_required=T, open
    position, mark absent, two conflicting T-60 candles => mark None and no
    ambiguous marked equity anywhere."""
    p = float(_sol_price())
    store = _open_sol(cfg, long_decision(p, 2000))
    snaps = snapshots_at(T1)
    snaps["SOL"] = None
    # a complete hour of candles ending in the conflicting duplicate pair:
    # replay reaches T (required==T via the clean [T0, T1) coverage from the
    # non-duplicated minutes)... the duplicate sits at T-60, so coverage
    # itself is anomalous — required stops before T and no mark may adopt.
    candles = _dup_t60(T1, p, close_a=100, close_b=200)
    caller = ScriptedCaller({})
    recovery.run_checkpointed(
        T1, snaps, caller, cfg, store,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": candles}})
    accounts, meta = persistence.load_state(store + "/state.json")
    assert meta["marks"]["SOL"] is None              # never 100 nor 200
    a = accounts["sol_haiku_raw"]
    assert a["qty"] != 0                             # position intact
    pt = meta["equity_history"]["sol_haiku_raw"][-1]
    assert pt["equity"] is None                      # honest null, not marked
    assert not [c for c in caller.calls if c["id"].startswith("sol")]
    # ALSO the pure crash-resume shape: force the exact meta the mentor
    # described (required==T, mark absent) and re-run mark adoption alone
    store2 = _open_sol(cfg, long_decision(p, 2000))
    spath2 = store2 + "/state.json"
    accounts2, meta2 = persistence.load_state(spath2,
                                              expect_full_roster=True)
    meta2.update({"boundary": T1, "phase": "pre_replay",
                  "finalized_pairs": {}, "boundary_complete": False,
                  "marks": {"BTC": None, "ETH": None, "SOL": None},
                  "marks_T": T1,
                  "replay_next_required": {"SOL": T1}})
    persistence.save_state(spath2, accounts2, meta2)
    recovery.run_checkpointed(
        T1, {**snapshots_at(T1), "SOL": None}, ScriptedCaller({}), cfg,
        store2,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": candles}})
    _, meta2b = persistence.load_state(spath2)
    assert meta2b["marks"]["SOL"] is None            # resume adopts nothing


def test_single_valid_t60_candle_still_adopts_mark(cfg):
    """Required test 3: exactly one valid T-60 candle => adoption succeeds."""
    p = float(_sol_price())
    store = _open_sol(cfg, long_decision(p, 2000))
    snaps = snapshots_at(T1)
    snaps["SOL"] = None
    candles = mk_candles(T0, 60, p)
    recovery.run_checkpointed(
        T1, snaps, ScriptedCaller({}), cfg, store,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": candles}})
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["marks"]["SOL"] == str(candles[-1]["c"])
    accounts, _ = persistence.load_state(store + "/state.json")
    pt = meta["equity_history"]["sol_haiku_raw"][-1]
    assert Decimal(pt["equity"]) == state.equity_at(
        accounts["sol_haiku_raw"], candles[-1]["c"])


def test_zero_t60_candles_mark_stays_none(cfg):
    """Required test 4: complete-looking data that stops before T-60 =>
    mark remains None."""
    p = float(_sol_price())
    store = _open_sol(cfg, long_decision(p, 2000))
    snaps = snapshots_at(T1)
    snaps["SOL"] = None
    candles = mk_candles(T0, 59, p)                  # ends at T1-120
    recovery.run_checkpointed(
        T1, snaps, ScriptedCaller({}), cfg, store,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": candles}})
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["marks"]["SOL"] is None
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"
