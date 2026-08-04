"""Mentor Ruling 018 remediation regressions: independent production 1m
replay fetch, removal of contradictory configuration fields, and mandatory
durable trust-record validation on unstarted/started/complete restarts.
All offline and deterministic."""
import json
import os
import time

import pytest

from conftest import T0, ScriptedCaller, load_fix, long_decision
from engine import config, official, persistence, recovery
from test_official import direct, provisioned_official, run_official
from test_ruling010 import PilotClock, digests
from test_ruling011 import _sol_price
from test_ruling014 import NeverPublish
from test_ruling016 import (_load_script, _passing_report, mk_candles,
                            snapshots_at, _prod_store)
from test_ruling017 import LifecycleRecorder

HOUR = 3600
T1, T2 = T0 + HOUR, T0 + 2 * HOUR
PAIR_TO_COIN = {"XBTUSD": "BTC", "ETHUSD": "ETH", "SOLUSD": "SOL"}


# ---- 018.1 production 1m replay fetch independent of prompt data ----

def _kraken_stub(sol_1m=None, fail=frozenset()):
    """Stands in for the network layer ONLY; the production fetch_market()
    parsing/splitting logic runs for real. `fail` holds (pair, interval)
    tuples; `sol_1m` optionally overrides the SOL 1m series."""
    def kraken_ohlc(pair, interval_min):
        if (pair, interval_min) in fail:
            raise RuntimeError("kraken transport down")
        coin = PAIR_TO_COIN[pair]
        if coin == "SOL" and interval_min == 1 and sol_1m is not None:
            return sol_1m
        tf = {1: "1m", 60: "1h", 1440: "1d"}[interval_min]
        return load_fix(coin, tf)
    return kraken_ohlc


def _open_sol(cfg, decision=None):
    store = _prod_store()
    d = decision or long_decision(float(_sol_price()), 2000)
    recovery.run_checkpointed(T0, snapshots_at(T0),
                              ScriptedCaller({"sol_haiku_raw": dict(d)}),
                              cfg, store)
    return store


@pytest.mark.parametrize("fail_interval,move,reason", [
    (60, "dip_to", "stop_loss"),                 # 1h fails -> stop still runs
    (1440, "rise_to", "take_profit"),            # 1d fails -> tp still runs
])
def test_prompt_data_failure_never_discards_valid_1m(cfg, monkeypatch,
                                                     fail_interval, move,
                                                     reason):
    mod = _load_script("run_official_14d")
    p = float(_sol_price())
    factor = 0.968 if move == "dip_to" else 1.06
    sol_1m = mk_candles(T0, 120, p, at=30, **{move: p * factor})
    monkeypatch.setattr(mod, "kraken_ohlc", _kraken_stub(
        sol_1m=sol_1m, fail={("SOLUSD", fail_interval)}))
    snap, spec = mod.fetch_market("SOL", T1, False)      # PRODUCTION path
    assert snap is None                          # prompts blocked
    assert spec["start"] == T0 and spec["end"] == T1
    assert len(spec["candles"]) == 120           # valid 1m data RETAINED
    store = _open_sol(cfg)
    caller = ScriptedCaller({})
    snaps = snapshots_at(T1)
    snaps["SOL"] = snap
    ledger, _, _ = recovery.run_checkpointed(
        T1, snaps, caller, cfg, store, pre_replay_spec={"SOL": spec})
    accounts, meta = persistence.load_state(store + "/state.json")
    tr = accounts["sol_haiku_raw"]["trades"][0]
    assert tr["reason"] == reason                # resting exit EXECUTED
    assert not [c for c in caller.calls if c["id"].startswith("sol")]
    sol = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert sol["reason"] == "DATA_UNAVAILABLE"   # honest prompt block
    assert meta["replay_state"]["SOL"]["status"] == "REPLAY_COMPLETE"


def test_true_1m_failure_still_raises_and_creates_catchup(cfg, monkeypatch):
    mod = _load_script("run_official_14d")
    monkeypatch.setattr(mod, "kraken_ohlc",
                        _kraken_stub(fail={("SOLUSD", 1)}))
    with pytest.raises(RuntimeError):            # total failure => raise
        mod.fetch_market("SOL", T1, False)
    # full production path: run_official + the real fetch_market wiring
    store = provisioned_official(n=2)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    caller = ScriptedCaller({})
    official.run_official(store, cfg, caller, mod.fetch_market, dp, vc,
                          vc.sleep)
    _, meta = persistence.load_state(os.path.join(store, "state.json"))
    assert meta["replay_next_required"]["SOL"] == T0     # exact gap persisted
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"
    assert not [c for c in caller.calls if c["id"].startswith("sol")]
    assert [c for c in caller.calls if c["id"].startswith("btc")]  # others ran


def test_fetch_market_split_is_structural():
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    body = src[src.index("def fetch_market"):src.index("def live_caller")]
    assert body.index("kraken_ohlc(pair, 1)") \
        < body.index('"candles"') < body.index("kraken_ohlc(pair, 60)")
    assert "snap = None" in body                 # 1h/1d failure != 1m failure


# ---- 018.2 contradictory configuration removed ----

def test_old_contradictory_fields_and_phrases_are_gone(cfg):
    col = cfg["collection"]
    for stale in ("collection_deadline_seconds", "global_collection_deadline",
                  "deadline_note"):
        assert stale not in col
    blob = json.dumps(cfg)
    for phrase in ("one common post-T", "T+12 minutes",
                   "anchored at collection start", "post_T_replay",
                   "post-T"):
        assert phrase not in blob, phrase
    assert "pre-decision replay" in col["procedure"][0]
    assert "NO post-decision replay" in col["procedure"][-1]
    pas = cfg["pair_abort_semantics"]
    assert "post_T_replay_continues_for_both_accounts" not in pas
    assert "pre-decision replay" in pas["replay_continues_for_both_accounts"]


def test_separate_machine_readable_deadline_concepts(cfg):
    col = cfg["collection"]
    assert col["model_collection_deadline_seconds_after_T"] == 510 \
        == official.COLLECTION_DEADLINE_S
    assert col["hard_terminal_deadline_seconds_after_T"] == 720 \
        == official.HARD_DEADLINE_S
    assert official.COLLECTION_DEADLINE_S != official.HARD_DEADLINE_S
    for rel in ("engine/pilot.py", "engine/recovery.py"):
        src = open(os.path.join(config.ROOT, rel)).read()
        assert 'cfg["collection"]["collection_deadline_seconds"]' not in src
    osrc = open(os.path.join(config.ROOT, "engine", "official.py")).read()
    assert "model_collection_deadline_seconds_after_T" in osrc
    assert "hard_terminal_deadline_seconds_after_T" in osrc


# ---- 018.3 mandatory durable trust record ----

def _trusted_store(tmp_path, n=2, ts=None, endpoint=None, start=T0):
    eng, site = digests()
    over = {}
    if endpoint is not None:
        over["canonical_endpoint"] = endpoint
    path, sha = _passing_report(tmp_path, eng, site, ts=ts,
                                name=f"report_{n}_{start}.json", **over)
    act = {"approved": "YES-OFFICIAL-RUN-APPROVED", "engine_digest": eng,
           "site_digest": site, "start_utc": start, "total": n,
           "preflight": {"report_path": path, "report_sha256": sha}}
    act_path = tmp_path / f"act_{n}_{start}.json"
    act_path.write_text(json.dumps(act))
    act_sha = official.activation_sha(str(act_path))
    import tempfile
    store = tempfile.mkdtemp(prefix="arena-r18-")
    official.provision_official(store, eng, site, start, total=n,
                                activation_sha=act_sha)
    official.archive_activation(store, act, act_sha)
    return store, str(act_path), act_sha


def test_valid_trust_record_verifies_read_only(cfg, tmp_path):
    store, _, _ = _trusted_store(tmp_path)
    before = {f: os.path.getsize(os.path.join(store, f))
              for f in os.listdir(store)}
    rec = official.verify_durable_trust(store, time.time())
    assert rec["start_utc"] == T0 and rec["total"] == 2
    after = {f: os.path.getsize(os.path.join(store, f))
             for f in os.listdir(store)}
    assert before == after                       # strictly read-only


def test_valid_48h_midrun_restart_without_control_file(cfg, tmp_path):
    now = time.time()
    store, act_path, _ = _trusted_store(tmp_path, ts=now - 30 * HOUR)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    rec1 = LifecycleRecorder(dp)
    with pytest.raises(recovery.CrashError):
        run_official(store, cfg, ScriptedCaller({}), rec1, vc,
                     crash_at="after_one_pair")
    os.remove(act_path)                          # external file gone
    # started restart: freshness WAIVED, everything else still verified
    assert official.verify_durable_trust(store, now, waive_freshness=True)
    with pytest.raises(official.PreflightAttestationError):
        official.verify_durable_trust(store, now, waive_freshness=False)
    vc.t += 48 * HOUR
    dp2, _ = direct(vc)
    rec2 = LifecycleRecorder(dp2)
    sched = run_official(store, cfg, ScriptedCaller({}), rec2, vc)
    assert len(sched["completed"]) == 2          # resumed
    assert "READY" not in rec2.lifecycles


MISSING_CASES = {
    "accepted_activation": official.ACCEPTED_ACTIVATION_NAME,
    "archived_preflight": official.ACCEPTED_PREFLIGHT_NAME,
    "launch_manifest": config.LAUNCH_MANIFEST_NAME,
    "site_manifest": config.SITE_MANIFEST_NAME,
    "binding": official.BINDING_NAME,
}


@pytest.mark.parametrize("case", sorted(MISSING_CASES))
def test_missing_durable_trust_evidence_refuses(cfg, tmp_path, case):
    store, _, _ = _trusted_store(tmp_path)
    os.remove(os.path.join(store, MISSING_CASES[case]))
    with pytest.raises((official.TrustRecordError, config.IntegrityError,
                        official.PreflightAttestationError)):
        official.verify_durable_trust(store, time.time(),
                                      waive_freshness=True)


def test_modified_preflight_copy_refuses(cfg, tmp_path):
    store, _, _ = _trusted_store(tmp_path)
    with open(os.path.join(store, official.ACCEPTED_PREFLIGHT_NAME), "a") as f:
        f.write(" ")
    with pytest.raises(official.PreflightAttestationError):
        official.verify_durable_trust(store, time.time(),
                                      waive_freshness=True)


def test_mismatched_trust_fields_refuse(cfg, tmp_path):
    # engine digest tampered in the accepted record
    store, _, _ = _trusted_store(tmp_path)
    apath = os.path.join(store, official.ACCEPTED_ACTIVATION_NAME)
    stored = json.load(open(apath))
    stored["record"]["engine_digest"] = "f" * 64
    json.dump(stored, open(apath, "w"))
    with pytest.raises(official.TrustRecordError):
        official.verify_durable_trust(store, time.time(),
                                      waive_freshness=True)
    # start/total mismatch vs the persisted schedule
    store2, _, _ = _trusted_store(tmp_path, n=2, start=T0 + HOUR)
    apath2 = os.path.join(store2, official.ACCEPTED_ACTIVATION_NAME)
    stored2 = json.load(open(apath2))
    stored2["record"]["start_utc"] = T0
    json.dump(stored2, open(apath2, "w"))
    with pytest.raises(official.TrustRecordError):
        official.verify_durable_trust(store2, time.time(),
                                      waive_freshness=True)
    # endpoint mismatch inside the attested report
    store3, _, _ = _trusted_store(tmp_path, n=3,
                                  endpoint="https://evil.example/x.js")
    with pytest.raises(official.PreflightAttestationError):
        official.verify_durable_trust(store3, time.time(),
                                      waive_freshness=True)
    # binding tampered
    store4, _, _ = _trusted_store(tmp_path, n=4)
    bpath = os.path.join(store4, official.BINDING_NAME)
    b = json.load(open(bpath))
    b["activation_sha"] = "0" * 64
    json.dump(b, open(bpath, "w"))
    with pytest.raises(official.TrustRecordError):
        official.verify_durable_trust(store4, time.time(),
                                      waive_freshness=True)


def test_completed_run_validates_trust_then_reports_complete(cfg, tmp_path):
    store, _, _ = _trusted_store(tmp_path, n=1)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    run_official(store, cfg, ScriptedCaller({}), dp, vc)
    assert official.classify_official_store(store) == "complete"
    assert official.verify_durable_trust(store, time.time(),
                                         waive_freshness=True)
    caller = ScriptedCaller({})
    official.run_official(store, cfg, caller, None, NeverPublish(), vc,
                          vc.sleep)
    assert caller.calls == []                    # zero calls, zero pubs


def test_runner_validates_trust_before_any_action():
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    i_trust = src.index("validate_trust_or_halt(kind)")
    assert src.index("verify_store_integrity()") < i_trust
    assert i_trust < src.index('kind == "complete"')
    assert i_trust < src.index("official.reconcile_unstarted_schedule")
    assert i_trust < src.index("run_official(")
    fn = src[src.index("def validate_trust_or_halt"):
             src.index("def main")]
    assert "verify_durable_trust" in fn and "sys.exit(3)" in fn
    assert "INTEGRITY_HALT" in fn                # public health reflects it
