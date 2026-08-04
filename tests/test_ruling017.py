"""Mentor Ruling 017 remediation regressions: started-run restart recovery,
never-skipped replay intervals (exact required-minute tracking), strict
preflight attestation verification, and the reconciled deadline/catch-up
configuration. All offline and deterministic."""
import json
import os
import tempfile
import time

import pytest

from conftest import T0, ScriptedCaller, long_decision
from engine import config, official, persistence, pilot, recovery, state
from test_official import direct, provisioned_official, run_official
from test_ruling010 import PilotClock, digests, parse_payload
from test_ruling011 import _sol_price
from test_ruling014 import NeverPublish
from test_ruling016 import (_act, _passing_report, mk_candles, snapshots_at,
                            _prod_store)

HOUR = 3600
T1, T2 = T0 + HOUR, T0 + 2 * HOUR


class LifecycleRecorder:
    """Wraps a DirectPublisher; records every published lifecycle."""

    def __init__(self, dp):
        self.dp = dp
        self.deadline = None
        self.lifecycles = []

    def __call__(self, text):
        self.lifecycles.append(parse_payload(text).get("round_lifecycle"))
        self.dp.deadline = self.deadline
        return self.dp(text)


# ---- 017.1 started-run restart recovery ----

def test_classify_official_store_all_states(cfg):
    store = tempfile.mkdtemp(prefix="arena-r17-")
    persistence.save_state(os.path.join(store, "state.json"),
                           state.init_accounts(), {"boundary": None})
    assert official.classify_official_store(store) == "no_schedule"
    eng, site = digests()
    official.provision_official(store, eng, site, T0, total=2)
    assert official.classify_official_store(store) == "unstarted"
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    with pytest.raises(recovery.CrashError):
        run_official(store, cfg, ScriptedCaller({}), dp, vc,
                     crash_at="after_one_pair")
    assert official.classify_official_store(store) == "started"
    dp2, _ = direct(vc)
    run_official(store, cfg, ScriptedCaller({}), dp2, vc)
    assert official.classify_official_store(store) == "complete"


def _started_store(cfg, vc):
    """A schedule of 2 with boundary 1 crashed mid-way (STARTED)."""
    store = provisioned_official(n=2)
    dp, _ = direct(vc)
    rec = LifecycleRecorder(dp)
    with pytest.raises(recovery.CrashError):
        run_official(store, cfg, ScriptedCaller({}), rec, vc,
                     crash_at="after_one_pair")
    assert "READY" in rec.lifecycles                 # first start published it
    return store


def test_reboot_48h_later_resumes_same_schedule_without_ready(cfg):
    vc = PilotClock(float(T0) - 60)
    store = _started_store(cfg, vc)
    sched0 = pilot.load_schedule(store)
    vc.t += 48 * HOUR                                # long outage
    dp2, _ = direct(vc)
    rec2 = LifecycleRecorder(dp2)
    sched = run_official(store, cfg, ScriptedCaller({}), rec2, vc)
    assert sched["boundaries"] == sched0["boundaries"]   # same schedule
    assert len(sched["completed"]) == 2              # all terminal (honest)
    assert "READY" not in rec2.lifecycles            # never republished
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    reasons = {e.get("reason") for e in ledger if e.get("status")}
    assert "crash_recovery" in reasons               # frozen recovery rule


def test_reboot_with_activation_deleted_after_start_still_resumes(cfg,
                                                                  tmp_path):
    vc = PilotClock(float(T0) - 60)
    store = _started_store(cfg, vc)
    gone = str(tmp_path / "deleted_activation.json")   # never exists
    dp2, _ = direct(vc)
    sched = run_official(
        store, cfg, ScriptedCaller({}), dp2, vc,
        disarm_check=official.make_disarm_check(gone, "ab" * 32))
    assert len(sched["completed"]) == 2              # NOT disarmed mid-run


def test_resume_uses_durable_attestation_not_scratch_report(cfg, tmp_path):
    vc = PilotClock(float(T0) - 60)
    store = _started_store(cfg, vc)
    eng, site = digests()
    path, sha = _passing_report(tmp_path, eng, site)
    act = {"engine_digest": eng, "site_digest": site, "start_utc": T0,
           "total": 2, "preflight": {"report_path": path,
                                     "report_sha256": sha}}
    official.archive_activation(store, act, "cd" * 32)
    os.remove(path)                                  # scratch report gone
    stored = json.load(open(os.path.join(
        store, official.ACCEPTED_ACTIVATION_NAME)))
    assert stored["record"]["preflight"]["report_sha256"] == sha
    assert os.path.exists(os.path.join(store,
                                       official.ACCEPTED_PREFLIGHT_NAME))
    dp2, _ = direct(vc)
    sched = run_official(store, cfg, ScriptedCaller({}), dp2, vc)
    assert len(sched["completed"]) == 2              # resumed regardless


def test_completed_reboot_reports_complete_with_zero_activity(cfg):
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    run_official(store, cfg, ScriptedCaller({}), dp, vc)
    assert official.classify_official_store(store) == "complete"
    caller = ScriptedCaller({})
    hdir = tempfile.mkdtemp(prefix="arena-h17-")
    official.run_official(store, cfg, caller, None, NeverPublish(), vc,
                          vc.sleep, health_dir=hdir)
    assert caller.calls == []                        # zero model calls
    assert json.load(open(os.path.join(hdir, "health.json")))["state"] \
        == "COMPLETE"


def test_runner_script_state_machine_and_integrity_order():
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    i_int = src.index("verify_store_integrity()")
    i_cls = src.index("official.classify_official_store")
    i_rec = src.index("official.reconcile_unstarted_schedule")
    assert i_int < i_cls < i_rec                     # integrity FIRST
    assert 'kind == "complete"' in src and 'kind == "started"' in src
    assert "archive_activation" in src               # durable before start
    # the started-resume path never consults armed_off_loop
    started_block = src[src.index('kind == "started"'):i_rec]
    assert "armed_off_loop" not in started_block
    assert "verify_preflight_attestation" not in started_block


# ---- 017.2 never skip a missing replay interval ----

def _snaps_sol_down(T):
    snaps = snapshots_at(T)
    snaps["SOL"] = None
    return snaps


def _empty_spec(start, end):
    return {"SOL": {"start": start, "end": end, "candles": []}}


def _b1_open_long(cfg, decision=None):
    store = _prod_store()
    d = decision or long_decision(float(_sol_price()), 2000)
    recovery.run_checkpointed(T0, snapshots_at(T0),
                              ScriptedCaller({"sol_haiku_raw": dict(d)}),
                              cfg, store)
    return store


def _sol_calls(caller):
    return [c["id"] for c in caller.calls if c["id"].startswith("sol")]


def test_total_fetch_failure_persists_exact_required_minute(cfg):
    store = _b1_open_long(cfg)
    caller = ScriptedCaller({})
    ledger, _, _ = recovery.run_checkpointed(
        T1, _snaps_sol_down(T1), caller, cfg, store,
        pre_replay_spec=_empty_spec(T0, T1))
    assert _sol_calls(caller) == []                  # zero model calls
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["replay_next_required"]["SOL"] == T0     # EXACT minute
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"
    assert meta["replay_state"]["SOL"]["gap_since"] == T0
    sol = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert sol["status"] == "PAIR_ABORTED"
    assert sol["reason"] == "DATA_UNAVAILABLE"


EXITS = {
    "stop": (lambda p: long_decision(p, 2000),
             lambda p: dict(dip_to=p * 0.968), "stop_loss"),
    "take_profit": (lambda p: long_decision(p, 2000),
                    lambda p: dict(rise_to=p * 1.06), "take_profit"),
}


@pytest.mark.parametrize("kind", sorted(EXITS))
def test_missed_hour_exit_executes_before_next_prompt(cfg, kind):
    make_dec, make_move, reason = EXITS[kind]
    p = float(_sol_price())
    store = _b1_open_long(cfg, make_dec(p))
    caller1 = ScriptedCaller({})
    recovery.run_checkpointed(T1, _snaps_sol_down(T1), caller1, cfg, store,
                              pre_replay_spec=_empty_spec(T0, T1))
    assert _sol_calls(caller1) == []
    # feed recovers at T2 with the FULL missing history [T0, T2)
    candles = mk_candles(T0, 120, p, at=30, **make_move(p))
    caller2 = ScriptedCaller({})
    recovery.run_checkpointed(
        T2, snapshots_at(T2), caller2, cfg, store,
        pre_replay_spec={"SOL": {"start": T1, "end": T2, "candles": candles}})
    accounts, meta = persistence.load_state(store + "/state.json")
    tr = accounts["sol_haiku_raw"]["trades"][0]
    assert tr["reason"] == reason
    assert T0 <= tr["closed_ts"] < T1                # executed IN the gap
    user = persistence.read_prompt_archive(
        store, f"v1-ALL-{T2}")["sol_haiku_raw"]["user"]
    assert "flat — no position" in user              # exit BEFORE the prompt
    assert meta["replay_state"]["SOL"]["status"] == "REPLAY_COMPLETE"
    assert meta["replay_next_required"]["SOL"] == T2


def test_missed_hour_invalidation_latches_before_next_prompt(cfg):
    p = float(_sol_price())
    d = dict(long_decision(p, 2000), stop_loss=None, take_profit=None,
             invalidation={"timeframe": "1m_intrabar",
                           "operator": "price_at_or_below",
                           "level": p * 0.98})
    store = _b1_open_long(cfg, d)
    recovery.run_checkpointed(T1, _snaps_sol_down(T1), ScriptedCaller({}),
                              cfg, store,
                              pre_replay_spec=_empty_spec(T0, T1))
    candles = mk_candles(T0, 120, p, dip_to=p * 0.975, at=30)
    hold = dict(long_decision(p, 2000), stop_loss=None, take_profit=None,
                invalidation=None)
    recovery.run_checkpointed(
        T2, snapshots_at(T2), ScriptedCaller({"sol_haiku_raw": hold}), cfg,
        store,
        pre_replay_spec={"SOL": {"start": T1, "end": T2, "candles": candles}})
    user = persistence.read_prompt_archive(
        store, f"v1-ALL-{T2}")["sol_haiku_raw"]["user"]
    assert "Status: TRIGGERED" in user               # latched in the gap
    accounts, _ = persistence.load_state(store + "/state.json")
    lc = accounts["sol_haiku_raw"]["lifecycle"]
    assert lc["triggered"] is not None
    assert T0 <= lc["triggered"]["t"] < T1


def test_data_starting_after_required_minute_is_refused(cfg):
    p = float(_sol_price())
    store = _b1_open_long(cfg)
    recovery.run_checkpointed(T1, _snaps_sol_down(T1), ScriptedCaller({}),
                              cfg, store,
                              pre_replay_spec=_empty_spec(T0, T1))
    # recovery supplies only [T1, T2): starts AFTER the required minute T0
    caller = ScriptedCaller({})
    ledger, _, _ = recovery.run_checkpointed(
        T2, snapshots_at(T2), caller, cfg, store,
        pre_replay_spec={"SOL": {"start": T1, "end": T2,
                                 "candles": mk_candles(T1, 60, p)}})
    assert _sol_calls(caller) == []                  # still blocked
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["replay_state"]["SOL"]["status"] != "REPLAY_COMPLETE"
    assert meta["replay_next_required"]["SOL"] == T0     # never advanced
    sol = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert sol["reason"] == "DATA_UNAVAILABLE"
    accounts, _ = persistence.load_state(store + "/state.json")
    assert accounts["sol_haiku_raw"]["trades"] == []     # nothing skipped


def test_snapshot_failure_with_complete_1m_still_replays(cfg):
    """1m replay availability is independent of hourly/daily prompt-snapshot
    availability: resting stops execute even when prompts are blocked."""
    p = float(_sol_price())
    store = _b1_open_long(cfg)
    caller = ScriptedCaller({})
    candles = mk_candles(T0, 60, p, dip_to=p * 0.968, at=30)
    ledger, _, _ = recovery.run_checkpointed(
        T1, _snaps_sol_down(T1), caller, cfg, store,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": candles}})
    assert _sol_calls(caller) == []                  # prompts still blocked
    accounts, meta = persistence.load_state(store + "/state.json")
    tr = accounts["sol_haiku_raw"]["trades"][0]
    assert tr["reason"] == "stop_loss"               # stop maintained
    assert meta["replay_state"]["SOL"]["status"] == "REPLAY_COMPLETE"
    sol = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert sol["reason"] == "DATA_UNAVAILABLE"       # honest prompt block


@pytest.mark.parametrize("hours", [2, 9])
def test_multi_hour_outage_catches_up_exactly(cfg, hours):
    p = float(_sol_price())
    store = _b1_open_long(cfg)
    for i in range(1, hours + 1):
        Ti = T0 + i * HOUR
        recovery.run_checkpointed(
            Ti, _snaps_sol_down(Ti), ScriptedCaller({}), cfg, store,
            pre_replay_spec=_empty_spec(Ti - HOUR, Ti))
        _, meta = persistence.load_state(store + "/state.json")
        assert meta["replay_next_required"]["SOL"] == T0   # pinned at T0
    Tn = T0 + (hours + 1) * HOUR
    candles = mk_candles(T0, (hours + 1) * 60, p)
    recovery.run_checkpointed(
        Tn, snapshots_at(Tn), ScriptedCaller({}), cfg, store,
        pre_replay_spec={"SOL": {"start": Tn - HOUR, "end": Tn,
                                 "candles": candles}})
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["replay_state"]["SOL"]["status"] == "REPLAY_COMPLETE"
    assert meta["replay_next_required"]["SOL"] == Tn      # EXACT catch-up
    assert meta["replay_watermark"]["SOL"] == Tn - 60


def test_eleven_hour_unresolved_gap_terminates_coin(cfg):
    store = _b1_open_long(cfg)
    terminated = False
    for i in range(1, 12):                           # 11 failed hours
        Ti = T0 + i * HOUR
        ledger, _, _ = recovery.run_checkpointed(
            Ti, _snaps_sol_down(Ti), ScriptedCaller({}), cfg, store,
            pre_replay_spec=_empty_spec(Ti - HOUR, Ti))
        _, meta = persistence.load_state(store + "/state.json")
        if meta["coin_terminated"].get("SOL"):
            terminated = True
            assert i * HOUR > 10 * HOUR              # beyond the frozen limit
            break
    assert terminated
    assert meta["replay_state"]["SOL"]["status"] == "COIN_TERMINATED"


def test_official_passes_empty_spec_on_total_fetch_failure(cfg, monkeypatch):
    seen = {}
    real = recovery.run_checkpointed

    def spy(T, snaps, caller, cfg_, store_, **kw):
        seen["pre"] = kw.get("pre_replay_spec")
        return real(T, snaps, caller, cfg_, store_, **kw)
    monkeypatch.setattr(recovery, "run_checkpointed", spy)
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    from test_ruling010 import fake_fetch

    def failing_fetch(coin, T, first):
        if coin == "SOL":
            raise RuntimeError("kraken down")
        return fake_fetch(coin, T, first)
    official.run_official(store, cfg, ScriptedCaller({}), failing_fetch, dp,
                          vc, vc.sleep)
    assert seen["pre"]["SOL"]["candles"] == []       # explicit empty interval
    assert seen["pre"]["SOL"]["end"] == T0


# ---- 017.3 strict preflight attestation ----

def _verify(tmp_path, results=None, ts=None, start_utc=None, **over):
    eng, site = digests()
    now = time.time()
    path, sha = _passing_report(tmp_path, eng, site, ts=ts, results=results,
                                **over)
    act = _act(path, sha, eng, site)
    act["start_utc"] = int(start_utc if start_utc is not None
                           else now // HOUR * HOUR + HOUR)
    return official.verify_preflight_attestation(act, eng, site, now)


def test_full_strict_report_passes(tmp_path):
    assert _verify(tmp_path) is True


STRICT_SUMMARY_FAILS = {
    "model_calls_pass": {"model_calls_pass": False},
    "schema_valid": {"schema_valid": 17},
    "identity_ok": {"identity_ok": 17},
    "semantic": {"semantically_valid_first_try": 17},
    "transport": {"transport_failures": ["btc_haiku_raw"]},
    "archive": {"prompt_archive_durable": False},
    "separation": {"raw_ta_separation_ok": False},
    "unmutated": {"accounts_unmutated": False},
    "endpoint_ok": {"direct_endpoint_ok": False},
    "n": {"n": 17},
    "accepted": {"accepted": 17},
}


@pytest.mark.parametrize("case", sorted(STRICT_SUMMARY_FAILS))
def test_every_strict_summary_field_enforced(tmp_path, case):
    with pytest.raises(official.PreflightAttestationError):
        _verify(tmp_path, **STRICT_SUMMARY_FAILS[case])


def test_results_object_strictly_verified(tmp_path):
    from test_ruling016 import _full_results
    base = _full_results()
    missing = dict(base)
    missing.pop("sol_haiku_raw")
    extra = dict(base, extra_account=dict(base["btc_haiku_raw"]))
    not_accepted = json.loads(json.dumps(base))
    not_accepted["eth_opus_ta"]["accepted"] = False
    sem = json.loads(json.dumps(base))
    sem["btc_sonnet_raw"]["semantic_errors"] = ["late"]
    terr = json.loads(json.dumps(base))
    terr["eth_haiku_ta"]["transport_error"] = "boom"
    wrong_model = json.loads(json.dumps(base))
    wrong_model["sol_opus_raw"]["response_model"] = "claude-imposter"
    for bad in (missing, extra, not_accepted, sem, terr, wrong_model):
        with pytest.raises(official.PreflightAttestationError):
            _verify(tmp_path, results=bad)


def test_timestamp_policy(tmp_path):
    now = time.time()
    with pytest.raises(official.PreflightAttestationError):
        _verify(tmp_path, ts=now + official.CLOCK_TOLERANCE_S + 100)  # future
    assert _verify(tmp_path, ts=now + 60) is True    # inside tolerance
    with pytest.raises(official.PreflightAttestationError):
        _verify(tmp_path, ts=now - official.PREFLIGHT_VALIDITY_S - 60)  # stale
    with pytest.raises(official.PreflightAttestationError):   # T0 too late
        _verify(tmp_path,
                start_utc=int(now + official.PREFLIGHT_VALIDITY_S + HOUR))
    assert _verify(tmp_path,
                   start_utc=int(now // HOUR * HOUR + 2 * HOUR)) is True


# ---- 017.4 reconciled configuration contract ----

def test_config_deadlines_match_official_constants(cfg):
    col = cfg["collection"]
    assert col["model_collection_deadline_seconds_after_T"] \
        == official.COLLECTION_DEADLINE_S == 510
    assert col["resolution_replay_deadline_seconds_after_T"] \
        == official.RESOLUTION_DEADLINE_S == 630
    assert col["final_publication_target_seconds_after_T"] \
        == official.FINAL_PUBLISH_S == 690
    assert col["hard_terminal_deadline_seconds_after_T"] \
        == official.HARD_DEADLINE_S == 720
    assert "collection_deadline_seconds" not in col     # old dual-use gone
    assert "SCHEDULED boundary T" in col["deadline_anchor"]


def test_config_replay_contract_matches_engine(cfg):
    col = cfg["collection"]
    assert "pre-decision replay" in col["official_live_replay"].lower()
    assert "no post-decision replay" in col["official_live_replay"].lower()
    fc = col["fetch_failure_catchup"]
    assert "replay_next_required" in fc
    assert "refused" in fc and "10-hour" in fc
    # actual phase ordering: pre-decision replay precedes prompt archival
    src = open(os.path.join(config.ROOT, "engine", "recovery.py")).read()
    assert src.index("PRE-DECISION REPLAY") \
        < src.index("durable prompt archive BEFORE any request")
    osrc = open(os.path.join(config.ROOT, "engine", "official.py")).read()
    assert "pre_replay_spec=spec" in osrc
    assert "replay_spec=" not in osrc.replace("pre_replay_spec=", "")
