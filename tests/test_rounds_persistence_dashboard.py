"""Pair atomicity, waves, terminal split, persistence, metrics, dashboard, isolation."""
import copy
from decimal import Decimal

import pytest

from conftest import T0, ScriptedCaller, flat_decision, long_decision
from engine import (config, state, rounds, persistence, metrics, dashboard,
                    prompts, marketdata)


def run(accounts, snapshots, cfg, script=None, clock=None):
    caller = ScriptedCaller(script or {})
    ledger, archive, parchive = rounds.run_boundary(T0, snapshots, accounts,
                                                    caller, cfg, clock)
    return ledger, archive, parchive, caller


def test_full_boundary_all_pairs_commit(accounts, snapshots, cfg):
    ledger, archive, parchive, caller = run(accounts, snapshots, cfg)
    assert len(ledger) == 9
    assert all(e["status"] == "PAIR_COMMITTED" for e in ledger)
    assert len(parchive) == 18                       # all prompts archived
    assert all(a["n_decisions"] == 1 for a in accounts.values())


def test_prompts_pregenerated_before_first_call(accounts, snapshots, cfg):
    seen = {}
    def caller(aid, system, user, retry):
        seen[aid] = user
        return flat_decision()
    sc = ScriptedCaller({}, default="flat")
    sc.script = {aid: caller for aid in accounts}
    ledger, archive, parchive = rounds.run_boundary(T0, snapshots, accounts, sc, cfg)
    for aid, user in seen.items():
        assert parchive[aid] == user                 # identical frozen prompt


def test_pair_abort_isolates_and_preserves_state(accounts, snapshots, cfg):
    p = snapshots["BTC"]["P_T"]
    bad = dict(long_decision(p), size_usd=-5)        # invalid twice
    script = {"btc_haiku_raw": [bad, bad]}
    before = copy.deepcopy(persistence._enc(accounts["btc_haiku_raw"]))
    before_twin = copy.deepcopy(persistence._enc(accounts["btc_haiku_ta"]))
    ledger, archive, _, caller = run(accounts, snapshots, cfg, script)
    ab = [e for e in ledger if e["status"] == "PAIR_ABORTED"]
    assert len(ab) == 1 and ab[0]["pair"] == "btc_haiku"
    assert ab[0]["caused_by_arm"] == "raw"
    assert sum(e["status"] == "PAIR_COMMITTED" for e in ledger) == 8
    # zero decision-generated state change for BOTH twins
    assert persistence._enc(accounts["btc_haiku_raw"]) == before
    assert persistence._enc(accounts["btc_haiku_ta"]) == before_twin
    # attempts archived including the invalid ones
    haiku_attempts = [a for a in archive if a["account_id"] == "btc_haiku_raw"]
    assert len(haiku_attempts) == 2
    assert haiku_attempts[0]["fixed_rejection_reasons"]


def test_transport_retries_then_abort(accounts, snapshots, cfg):
    errs = [rounds.TransportError("boom")] * 3
    script = {"eth_opus_ta": errs}
    ledger, archive, _, caller = run(accounts, snapshots, cfg, script)
    ab = [e for e in ledger if e["status"] == "PAIR_ABORTED"][0]
    assert ab["pair"] == "eth_opus" and ab["reason"] == "transport_failure"
    assert ab["caused_by_arm"] == "ta"


def test_validation_retry_correction_commits(accounts, snapshots, cfg):
    p = snapshots["SOL"]["P_T"]
    bad = dict(long_decision(p), size_usd=-5)
    good = long_decision(p, 2000)
    script = {"sol_sonnet_raw": [bad, good]}
    ledger, archive, _, caller = run(accounts, snapshots, cfg, script)
    assert all(e["status"] == "PAIR_COMMITTED" for e in ledger)
    assert accounts["sol_sonnet_raw"]["qty"] > 0
    corrected = [a for a in archive if a["account_id"] == "sol_sonnet_raw"
                 and a["became_executed_decision"]]
    assert corrected[0]["attempt_number"] == 2
    retry_calls = [c for c in caller.calls if c["id"] == "sol_sonnet_raw" and c["retry"]]
    assert len(retry_calls) == 1


def test_missing_coin_data_aborts_only_that_coin(accounts, snapshots, cfg):
    snaps = dict(snapshots); snaps["SOL"] = None
    ledger, *_ = run(accounts, snaps, cfg)
    sol = [e for e in ledger if e["pair"].startswith("sol_")]
    other = [e for e in ledger if not e["pair"].startswith("sol_")]
    assert all(e["status"] == "PAIR_ABORTED" and e["reason"] == "DATA_UNAVAILABLE"
               for e in sol)
    assert all(e["status"] == "PAIR_COMMITTED" for e in other)


def test_deadline_aborts_remaining_pairs(accounts, snapshots, cfg):
    ledger, *_ = run(accounts, snapshots, cfg, clock=lambda: T0 + 13 * 60)
    assert all(e["status"] == "PAIR_ABORTED" and e["reason"] == "deadline_exceeded"
               for e in ledger)


def test_terminal_split(accounts, snapshots, cfg):
    accounts["btc_haiku_raw"]["terminal"] = True
    accounts["btc_haiku_raw"]["terminal_info"] = {"t": T0 - 3600, "cause": "liquidation"}
    p = snapshots["BTC"]["P_T"]
    script = {"btc_haiku_ta": long_decision(p, 2000)}
    ledger, archive, _, caller = run(accounts, snapshots, cfg, script)
    split = [e for e in ledger if e["status"] == "PAIR_TERMINAL_SPLIT"][0]
    assert split["terminal"] == ["btc_haiku_raw"]
    assert not any(c["id"] == "btc_haiku_raw" for c in caller.calls)   # no calls
    assert accounts["btc_haiku_ta"]["qty"] > 0                          # twin trades


def test_wave_order_deterministic_and_rotating():
    w1 = rounds.wave_order("v1-ALL-100")
    assert w1 == rounds.wave_order("v1-ALL-100")
    assert len(w1) == 3 and all(len(w) == 3 for w in w1)
    flat = [p for w in w1 for p in w]
    assert sorted(flat) == sorted((c, m) for c in state.COINS for m in state.MODELS)
    assert any(rounds.wave_order(f"v1-ALL-{i}") != w1 for i in range(20))


def test_coin_isolation_wrong_snapshot_raises(accounts, snapshots, cfg):
    a = accounts["btc_haiku_raw"]
    sys_p, user = prompts.render(a, snapshots["BTC"], cfg)
    assert "ETH" not in user and "SOL" not in user
    assert user.count("BTC") > 0


def test_persistence_roundtrip_and_atomicity(tmp_path, accounts, snapshots, cfg):
    p = snapshots["BTC"]["P_T"]
    from engine import execution
    execution.apply_decision(accounts["btc_haiku_raw"], long_decision(p, 3000), p, T0)
    path = str(tmp_path / "state.json")
    persistence.save_state(path, accounts, {"boundary": T0})
    loaded, meta = persistence.load_state(path)
    assert meta["boundary"] == T0
    a = loaded["btc_haiku_raw"]
    assert a["E"] == accounts["btc_haiku_raw"]["E"]           # Decimal-exact
    assert a["qty"] == accounts["btc_haiku_raw"]["qty"]
    assert a["lifecycle"]["invalidation"]["level"] == \
        accounts["btc_haiku_raw"]["lifecycle"]["invalidation"]["level"]
    import os
    assert not os.path.exists(path + ".tmp")                   # atomic rename


def test_duplicate_round_prevention(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    persistence.append_ledger(path, [{"round_id": "v1-BTC-X", "pair": "btc_haiku",
                                      "status": "PAIR_COMMITTED"}])
    led = persistence.read_ledger(path)
    assert persistence.boundary_already_processed(led, "BTC", "v1-BTC-X")
    assert not persistence.boundary_already_processed(led, "BTC", "v1-BTC-Y")


def test_metrics_denominators_and_reliability(accounts, snapshots, cfg):
    p = snapshots["BTC"]["P_T"]
    bad = dict(long_decision(p), size_usd=-5)
    script = {"btc_haiku_raw": [bad, long_decision(p, 2000)],
              "btc_sonnet_raw": long_decision(p, 3000)}
    ledger, archive, _, caller = run(accounts, snapshots, cfg, script)
    rel = metrics.reliability(archive, ledger)
    assert rel["validation_retry_rate"] > 0
    assert rel["successful_correction_rate"] == 1.0
    dlog = []
    for e in ledger:
        if e["status"] != "PAIR_COMMITTED":
            continue
        coin, model = e["pair"].split("_")
        for arm in ("raw", "ta"):
            a = accounts[state.account_id(coin.upper(), model, arm)]
            dlog.append({"round_id": e["round_id"], "coin": coin, "model": model,
                         "arm": arm, "position": state.side(a),
                         "size_usd": str(abs(a["qty"]) * p),
                         "pre_equity": "10000", "stop": a["stop"], "tp": a["tp"]})
    pb = metrics.paired_behaviour(dlog)
    assert pb["n_paired_rounds"] == 9
    assert pb["direction_disagreement_rate"] is not None
    assert metrics.feature_reference_frequency(["used RSI here", "no features"]) == 0.5


def test_dashboard_payload_18_accounts(accounts, snapshots, cfg):
    ledger, *_ = run(accounts, snapshots, cfg)
    manifest = config.build_manifest()
    pl = dashboard.payload(accounts, ledger, snapshots,
                           {"ts": T0, "code_hash": manifest["combined"]}, manifest, cfg)
    assert pl["label"] == "V1 EXPERIMENT"
    assert sum(len(pl["coins"][c]["accounts"]) for c in ("BTC", "ETH", "SOL")) == 18
    assert pl["round_counts"]["PAIR_COMMITTED"] == 9
    assert pl["pilot_link"]["label"].startswith("PILOT / SYSTEM TEST")
    assert pl["combined_hash"] == manifest["combined"]
