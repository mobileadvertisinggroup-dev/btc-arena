"""Crash & idempotency matrix (Ruling 005.5) over the checkpointed runner."""
import json

import pytest

from conftest import T0, ScriptedCaller, long_decision, load_fix
from engine import config, state, persistence, recovery, marketdata

CRASH_POINTS = ["after_prompts", "after_first_attempt", "after_validate",
                "after_execute", "after_finalize", "after_one_pair",
                "during_replay"]


def fresh_store(tmp_path, accounts=None):
    store = str(tmp_path)
    persistence.save_state(store + "/state.json", accounts or state.init_accounts(),
                           {"boundary": None})
    config.write_launch_manifest(store)
    return store


def candles_after(snapshots):
    out = {}
    for coin in ("BTC", "ETH", "SOL"):
        out[coin] = [c for c in marketdata.to_dec(load_fix(coin, "1m"))
                     if T0 <= c["t"] < T0 + 30 * 60]
    return out


def script_for(snapshots):
    s = {}
    for coin in ("BTC", "ETH", "SOL"):
        p = snapshots[coin]["P_T"]
        s[f"{coin.lower()}_haiku_raw"] = long_decision(p, 4000)
        s[f"{coin.lower()}_haiku_ta"] = long_decision(p, 4000)
    return s


def run(store, snapshots, cfg, crash_at=None, script=None):
    caller = ScriptedCaller(script or script_for(snapshots))
    ca = candles_after(snapshots)
    spec = {c: {"start": T0, "end": T0 + 30 * 60, "candles": ca[c]} for c in ca}
    return recovery.run_checkpointed(T0, snapshots, caller, cfg, store,
                                     crash_at=crash_at, replay_spec=spec)[0]


@pytest.mark.parametrize("point", CRASH_POINTS)
def test_crash_matrix_no_double_effects(tmp_path, snapshots, cfg, point):
    store = fresh_store(tmp_path)
    with pytest.raises(recovery.CrashError):
        run(store, snapshots, cfg, crash_at=point)
    accounts_mid, meta_mid = persistence.load_state(store + "/state.json")
    finalized_before = dict(meta_mid["finalized_pairs"])
    recovery.recover(store)
    ledger2 = run(store, snapshots, cfg)          # restart, no crash
    accounts, meta = persistence.load_state(store + "/state.json")
    assert meta["boundary_complete"]
    # finalized pairs remain finalized with the SAME status
    for pid, status in finalized_before.items():
        assert meta["finalized_pairs"][pid] == status
    # non-finalized pairs were aborted by the predeclared rule (never resumed)
    for e in ledger2:
        if e["pair"] not in finalized_before:
            assert e["status"] in ("PAIR_ABORTED",)
            assert e["reason"] == "crash_recovery"
    # invariants: fees/thesis/decisions applied at most once per account
    for a in accounts.values():
        assert a["n_decisions"] <= 1
        assert len(a["theses"]) <= 1
        assert a["E"] >= 0
        if a["qty"] == 0:
            assert a["entry"] is None and a["stop"] is None and a["tp"] is None
    # invalidation never latched twice: triggered is a single record
    for a in accounts.values():
        lc = a.get("lifecycle")
        if lc and lc["triggered"]:
            assert isinstance(lc["triggered"], dict)


def test_committed_pair_fees_charged_exactly_once(tmp_path, snapshots, cfg):
    store = fresh_store(tmp_path)
    with pytest.raises(recovery.CrashError):
        run(store, snapshots, cfg, crash_at="after_finalize")
    accounts_mid, meta_mid = persistence.load_state(store + "/state.json")
    finalized = [p for p, s in meta_mid["finalized_pairs"].items()
                 if s == "PAIR_COMMITTED"]
    fees_before = {aid: a["fees_total"] for aid, a in accounts_mid.items()}
    recovery.recover(store)
    run(store, snapshots, cfg)
    accounts, _ = persistence.load_state(store + "/state.json")
    for pid in finalized:
        coin, model = pid.split("_")
        for arm in ("raw", "ta"):
            aid = state.account_id(coin.upper(), model, arm)
            assert accounts[aid]["fees_total"] == fees_before[aid]   # unchanged
            assert accounts[aid]["n_decisions"] == accounts_mid[aid]["n_decisions"]


def test_crash_before_finalize_persists_nothing(tmp_path, snapshots, cfg):
    """after_validate and after_execute crash BEFORE the atomic finalize:
    the persisted state must show zero decision effects for that pair."""
    for point in ("after_validate", "after_execute"):
        (tmp_path / point).mkdir(exist_ok=True)
        store = fresh_store(tmp_path / point)
        with pytest.raises(recovery.CrashError):
            run(store, snapshots, cfg, crash_at=point)
        accounts, meta = persistence.load_state(store + "/state.json")
        unfinalized = [aid for aid, a in accounts.items()
                       if state.pair_id(a["coin"], a["model"]) not in
                       meta["finalized_pairs"]]
        for aid in unfinalized:
            a = accounts[aid]
            assert a["n_decisions"] == 0 and a["fees_total"] == 0
            assert a["qty"] == 0 and a["theses"] == []


def test_replay_watermark_prevents_double_application(tmp_path, snapshots, cfg):
    store = fresh_store(tmp_path)
    with pytest.raises(recovery.CrashError):
        run(store, snapshots, cfg, crash_at="during_replay")
    _, meta_mid = persistence.load_state(store + "/state.json")
    wm_mid = dict(meta_mid["replay_watermark"])
    assert wm_mid                                   # some replay persisted
    recovery.recover(store)
    run(store, snapshots, cfg)
    accounts, meta = persistence.load_state(store + "/state.json")
    # compare with a clean single-pass run from identical fresh state
    (tmp_path / "clean").mkdir(exist_ok=True)
    store2 = fresh_store(tmp_path / "clean")
    run(store2, snapshots, cfg)
    a2, m2 = persistence.load_state(store2 + "/state.json")
    for aid in accounts:
        pid = state.pair_id(accounts[aid]["coin"], accounts[aid]["model"])
        if meta["finalized_pairs"].get(pid) == "PAIR_COMMITTED" \
                and m2["finalized_pairs"].get(pid) == "PAIR_COMMITTED":
            assert accounts[aid]["E"] == a2[aid]["E"]        # no double exits/fees
            assert accounts[aid]["qty"] == a2[aid]["qty"]


def test_idempotent_rerun_after_completion(tmp_path, snapshots, cfg):
    store = fresh_store(tmp_path)
    run(store, snapshots, cfg)
    a1, m1 = persistence.load_state(store + "/state.json")
    run(store, snapshots, cfg)                      # same boundary again
    a2, m2 = persistence.load_state(store + "/state.json")
    assert json.dumps(persistence._enc_all(a1), sort_keys=True, default=str) == \
        json.dumps(persistence._enc_all(a2), sort_keys=True, default=str)
    for a in a2.values():
        assert a["n_decisions"] <= 1                # nothing executed twice
