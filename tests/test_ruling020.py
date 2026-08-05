"""Mentor Ruling 020 remediation regressions: strict-1m validation bypasses
closed (P_T source validation, duplicate/misorder replay anomalies) and the
fully recoverable pre-schedule provisioning transaction with TOCTOU-free
activation reads. All offline and deterministic."""
import json
import os
import tempfile
import time
from decimal import Decimal

import pytest

from conftest import T0, ScriptedCaller, load_fix, long_decision
from engine import config, marketdata, official, persistence, pilot, recovery
from test_official import direct, provisioned_official, run_official
from test_ruling010 import digests, PilotClock
from test_ruling011 import _sol_price
from test_ruling016 import _load_script, mk_candles, snapshots_at
from test_ruling018 import _kraken_stub, _open_sol
from test_ruling019 import _arm_args

HOUR = 3600
T1, T2 = T0 + HOUR, T0 + 2 * HOUR


# ---- 020.1a strict P_T source validation ----

def _sol_1m_with_bad_final(T, price, bad_close):
    """Valid series whose exact T-60 candle carries a malformed close."""
    out = mk_candles(T - 2 * HOUR, 120, price)
    assert out[-1]["t"] == T - 60
    out[-1]["c"] = Decimal(str(bad_close))
    out[-1]["l"] = Decimal(str(bad_close))
    return out


def test_invalid_T60_price_cannot_become_PT_production_path(cfg, monkeypatch):
    """The mentor's reproduced hole: T-60 close = -999 with valid 1h/1d must
    NOT produce a snapshot — DataUnavailable, no P_T."""
    mod = _load_script("run_official_14d")
    p = float(_sol_price())
    monkeypatch.setattr(mod, "kraken_ohlc", _kraken_stub(
        sol_1m=_sol_1m_with_bad_final(T1, p, -999)))
    snap, spec = mod.fetch_market("SOL", T1, False)
    assert snap is None                              # never P_T = -999


def test_invalid_T60_price_first_boundary(cfg):
    p = float(_sol_price())
    k1m = _sol_1m_with_bad_final(T0, p, -999)
    with pytest.raises(marketdata.DataUnavailable):
        marketdata.build_snapshot("SOL", k1m, load_fix("SOL", "1h"),
                                  load_fix("SOL", "1d"), T0)


def test_duplicate_T60_candle_is_ambiguous_no_PT(cfg):
    p = float(_sol_price())
    k1m = mk_candles(T0 - 2 * HOUR, 120, p)
    dup = dict(k1m[-1])
    dup["c"] = Decimal(str(p * 2))                   # conflicting duplicate
    k1m.append(dup)
    assert k1m[-1]["t"] == T0 - 60 == k1m[-2]["t"]
    with pytest.raises(marketdata.DataUnavailable):
        marketdata.build_snapshot("SOL", k1m, load_fix("SOL", "1h"),
                                  load_fix("SOL", "1d"), T0)


def test_invalid_PT_zero_model_calls_and_no_mark(cfg, monkeypatch):
    mod = _load_script("run_official_14d")
    p = float(_sol_price())
    bad_1m = _sol_1m_with_bad_final(T1, p, -999)
    monkeypatch.setattr(mod, "kraken_ohlc", _kraken_stub(sol_1m=bad_1m))
    snap, spec = mod.fetch_market("SOL", T1, False)
    store = _open_sol(cfg)
    caller = ScriptedCaller({})
    snaps = snapshots_at(T1)
    snaps["SOL"] = snap
    ledger, _, _ = recovery.run_checkpointed(
        T1, snaps, caller, cfg, store, pre_replay_spec={"SOL": spec})
    assert not [c for c in caller.calls if c["id"].startswith("sol")]
    _, meta = persistence.load_state(store + "/state.json")
    # the malformed T-60 candle is ALSO rejected as a mark and stops replay
    assert meta["marks"]["SOL"] is None
    sol = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert sol["reason"] == "DATA_UNAVAILABLE"
    accounts, _ = persistence.load_state(store + "/state.json")
    assert accounts["sol_haiku_raw"]["trades"] == []     # nothing executed


# ---- 020.1b duplicate-timestamp replay bypass ----

def _dup_at(candles, idx, **mutate):
    dup = dict(candles[idx])
    dup.update({k: Decimal(str(v)) for k, v in mutate.items()})
    return candles[:idx + 1] + [dup] + candles[idx + 1:]


@pytest.mark.parametrize("kind,mutate", [
    ("stop", {"l": None}),                           # filled below with stop
    ("target", {"h": None}),
    ("invalidation", {"l": None}),
])
def test_conflicting_duplicate_never_executes(cfg, kind, mutate):
    """The mentor's reproduced hole: two conflicting candles at the same
    minute, one of which would cross a resting level. NEITHER may execute;
    the duplicated minute becomes the gap."""
    p = float(_sol_price())
    if kind == "invalidation":
        d = dict(long_decision(p, 2000), stop_loss=None, take_profit=None,
                 invalidation={"timeframe": "1m_intrabar",
                               "operator": "price_at_or_below",
                               "level": p * 0.98})
    else:
        d = long_decision(p, 2000)
    store = _open_sol(cfg, d)
    candles = mk_candles(T0, 60, p)
    cross = {"stop": {"l": p * 0.9, "c": p},
             "target": {"h": p * 1.2},
             "invalidation": {"l": p * 0.9, "c": p}}[kind]
    candles = _dup_at(candles, 30, **cross)          # conflicting duplicate
    caller = ScriptedCaller({})
    recovery.run_checkpointed(
        T1, snapshots_at(T1), caller, cfg, store,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": candles}})
    accounts, meta = persistence.load_state(store + "/state.json")
    a = accounts["sol_haiku_raw"]
    assert a["trades"] == []                         # ZERO trades executed
    assert a["qty"] != 0
    if kind == "invalidation":
        assert a["lifecycle"]["triggered"] is None   # never latched
    # gap AT the duplicated minute; nothing at/after it executed
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"
    assert meta["replay_state"]["SOL"]["gap_since"] == T0 + 30 * 60
    assert meta["replay_next_required"]["SOL"] == T0 + 30 * 60
    assert not [c for c in caller.calls if c["id"].startswith("sol")]


def test_out_of_order_and_misaligned_never_execute(cfg):
    p = float(_sol_price())
    store = _open_sol(cfg)
    # out-of-order: a stop-crossing candle for an EARLIER minute reappears
    candles = mk_candles(T0, 60, p)
    late_dup = dict(candles[10])
    late_dup["l"] = Decimal(str(p * 0.9))
    candles = candles[:40] + [late_dup] + candles[40:]
    recovery.run_checkpointed(
        T1, snapshots_at(T1), ScriptedCaller({}), cfg, store,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": candles}})
    accounts, meta = persistence.load_state(store + "/state.json")
    assert accounts["sol_haiku_raw"]["trades"] == []
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"
    assert meta["replay_state"]["SOL"]["gap_since"] == T0 + 10 * 60
    # misaligned: crossing candle at a non-minute timestamp
    store2 = _open_sol(cfg)
    candles2 = mk_candles(T0, 60, p)
    candles2[30] = dict(candles2[30], t=candles2[30]["t"] + 30,
                        l=Decimal(str(p * 0.9)))
    recovery.run_checkpointed(
        T1, snapshots_at(T1), ScriptedCaller({}), cfg, store2,
        pre_replay_spec={"SOL": {"start": T0, "end": T1,
                                 "candles": candles2}})
    accounts2, meta2 = persistence.load_state(store2 + "/state.json")
    assert accounts2["sol_haiku_raw"]["trades"] == []
    assert meta2["replay_state"]["SOL"]["status"] != "REPLAY_COMPLETE"


def test_replay_complete_impossible_after_any_anomaly(cfg):
    p = float(_sol_price())
    store = _open_sol(cfg)
    candles = _dup_at(mk_candles(T0, 60, p), 59)     # anomaly at the very end
    recovery.run_checkpointed(
        T1, snapshots_at(T1), ScriptedCaller({}), cfg, store,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": candles}})
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"


def test_source_precision_still_exact(monkeypatch):
    import io
    mod = _load_script("run_official_14d")
    precise = "144.123456789012345678"
    rows = [[T0 - 60, precise, precise, precise, precise, "0", "1"]]
    payload = json.dumps({"result": {"SOLUSD": rows, "last": T0}}).encode()

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda url, timeout=0: FakeResp(payload))
    out = marketdata.to_dec(mod.kraken_ohlc("SOLUSD", 1))
    assert str(out[0]["c"]) == precise


# ---- 020.2 recoverable provisioning before a schedule exists ----

CRASH_POINTS = ["after_tx_marker", "after_launch_manifest",
                "after_site_manifest", "after_state", "after_schedule",
                "after_binding", "after_preflight_copy"]


def _snapshot_dir(store):
    return {f: open(os.path.join(store, f), "rb").read()
            for f in sorted(os.listdir(store))
            if os.path.isfile(os.path.join(store, f))}


@pytest.mark.parametrize("crash_at", CRASH_POINTS)
def test_crash_after_every_write_recovers_to_pretx_shape(cfg, tmp_path,
                                                         crash_at):
    """The mentor's reproduced hole included launch-manifest-only (no site
    manifest, no schedule). EVERY write point must restart to pristine
    ARMED/OFF — including pre-schedule crashes — and rearm cleanly."""
    act, sha, eng, site, blob = _arm_args(tmp_path, name=f"c_{crash_at}.json")
    store = tempfile.mkdtemp(prefix="arena-r20-")
    from engine import state as state_mod
    persistence.save_state(os.path.join(store, "state.json"),
                           state_mod.init_accounts(), {"boundary": None})
    before = _snapshot_dir(store)
    with pytest.raises((recovery.CrashError, RuntimeError)):
        official.arm_store(store, act, sha, eng, site, report_blob=blob,
                           crash_at=crash_at)
    assert not os.path.exists(os.path.join(
        store, official.ACCEPTED_ACTIVATION_NAME))   # never committed
    assert official.reconcile_incomplete_provisioning(store) \
        == "rolled_back_incomplete"
    after = _snapshot_dir(store)
    assert after == before                           # EXACT pre-tx shape
    official.verify_pristine_official_state(store)
    # clean rearm succeeds and validates fully
    sched = official.arm_store(store, act, sha, eng, site, report_blob=blob)
    assert sched["total"] == 2
    assert official.reconcile_incomplete_provisioning(store) == "committed"
    assert official.verify_durable_trust(store, time.time(),
                                         waive_freshness=True)
    assert not os.path.exists(os.path.join(store,
                                           official.PROVISIONING_TX_NAME))


def test_partial_manifest_crash_on_store_with_prior_manifests(cfg, tmp_path):
    """A data-v1-shaped store (manifests already present): a crash between
    manifest rewrites restores the ORIGINAL manifest bytes from the tx
    journal."""
    act, sha, eng, site, blob = _arm_args(tmp_path, name="prior.json")
    store = tempfile.mkdtemp(prefix="arena-r20p-")
    from engine import state as state_mod
    persistence.save_state(os.path.join(store, "state.json"),
                           state_mod.init_accounts(), {"boundary": None})
    config.provision_store(store, eng, site)         # pre-existing manifests
    before = _snapshot_dir(store)
    with pytest.raises(RuntimeError):
        official.arm_store(store, act, sha, eng, site, report_blob=blob,
                           crash_at="after_launch_manifest")
    assert official.reconcile_incomplete_provisioning(store) \
        == "rolled_back_incomplete"
    assert _snapshot_dir(store) == before            # bytes restored


def test_fully_committed_transaction_validates_and_resumes(cfg, tmp_path):
    act, sha, eng, site, blob = _arm_args(tmp_path, name="full.json")
    store = tempfile.mkdtemp(prefix="arena-r20f-")
    official.arm_store(store, act, sha, eng, site, report_blob=blob)
    assert official.reconcile_incomplete_provisioning(store) == "committed"
    assert official.verify_durable_trust(store, time.time())
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    sched = run_official(store, cfg, ScriptedCaller({}), dp, vc)
    assert len(sched["completed"]) == 2              # normal run


def test_marker_cleared_when_commit_raced_shutdown(cfg, tmp_path):
    """Crash between the accepted record and marker removal: the accepted
    record IS the commit — reconcile clears the stale marker and reports
    committed."""
    act, sha, eng, site, blob = _arm_args(tmp_path, name="race.json")
    store = tempfile.mkdtemp(prefix="arena-r20m-")
    official.arm_store(store, act, sha, eng, site, report_blob=blob)
    official._begin_provisioning_tx(store, sha)      # simulate stale marker
    assert official.reconcile_incomplete_provisioning(store) == "committed"
    assert not os.path.exists(os.path.join(store,
                                           official.PROVISIONING_TX_NAME))


def test_started_run_never_rolled_back_even_with_marker(cfg):
    store = provisioned_official(n=2)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    with pytest.raises(recovery.CrashError):
        run_official(store, cfg, ScriptedCaller({}), dp, vc,
                     crash_at="after_one_pair")
    official._begin_provisioning_tx(store, "00" * 32)
    assert official.reconcile_incomplete_provisioning(store) == "started"
    assert pilot.load_schedule(store)["boundaries"][0] == T0   # intact


def test_activation_read_is_toctou_free(tmp_path, monkeypatch):
    mod = _load_script("run_official_14d")
    monkeypatch.setattr(mod, "ACTIVATION", str(tmp_path / "act.json"))
    eng, site = digests()
    good = {"approved": "YES-OFFICIAL-RUN-APPROVED", "engine_digest": eng,
            "site_digest": site, "start_utc": T0, "total": 336,
            "preflight": {"report_path": "/x", "report_sha256": "ab" * 32}}
    (tmp_path / "act.json").write_text(json.dumps(good))
    act, sha = mod.read_activation()
    import hashlib
    assert sha == hashlib.sha256(
        (tmp_path / "act.json").read_bytes()).hexdigest()
    # swapping the file AFTER the read cannot change what was bound
    (tmp_path / "act.json").write_text(json.dumps(dict(good, start_utc=T1)))
    assert official.make_disarm_check(str(tmp_path / "act.json"), sha)() \
        is False                                     # swap detected
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    main_src = src[src.index("def main"):]
    assert "activation_sha(ACTIVATION)" not in main_src   # no re-read
    assert "act, act_sha = armed_off_loop()" in main_src


def test_startup_reconciles_before_integrity_verification():
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    main_src = src[src.index("def main"):]
    assert main_src.index("reconcile_incomplete_provisioning") \
        < main_src.index("verify_store_integrity()")
