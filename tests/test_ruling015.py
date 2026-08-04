"""Mentor Ruling 015 remediation regressions: pristine official-state launch
gate, disarm surviving service stop/reboot via activation binding,
transactional pair/replay resolution (no commit after T+630), strict
preflight PASS with production wave concurrency, and the hardened deployment
verifier. All offline."""
import importlib.util
import json
import os
import tempfile
import threading
from decimal import Decimal

import pytest

from conftest import T0, ScriptedCaller, flat_decision, long_decision
from engine import (config, official, persistence, pilot, publisher,
                    recovery, state)
from test_official import direct, provisioned_official, run_official
from test_ruling010 import PilotClock, digests
from test_ruling011 import _sol_price
from test_ruling014 import ManualClock

HOUR = 3600


def _load_script(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(config.ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pristine_store():
    store = tempfile.mkdtemp(prefix="arena-r15-")
    persistence.save_state(os.path.join(store, "state.json"),
                           state.init_accounts(), {"boundary": None})
    return store


def _dirty(mutate):
    """A correctly CHECKSUMMED but dirty state (the checksum is recomputed by
    save_state — exactly the case the gate must still refuse)."""
    store = _pristine_store()
    spath = os.path.join(store, "state.json")
    accounts, meta = persistence.load_state(spath, expect_full_roster=True)
    mutate(accounts, meta)
    persistence.save_state(spath, accounts, meta)
    return store


DIRTY_CASES = {
    "equity": lambda a, m: a["btc_haiku_raw"].update(E=Decimal("9999.99")),
    "position": lambda a, m: a["eth_sonnet_ta"].update(
        qty=Decimal("0.5"), entry=Decimal("1800")),
    "trade": lambda a, m: a["sol_opus_raw"]["trades"].append(
        {"pnl": "1", "fee": "0"}),
    "fees": lambda a, m: a["btc_haiku_ta"].update(fees_total=Decimal("0.01")),
    "lifecycle": lambda a, m: a["eth_haiku_raw"]["lifecycles"].append(
        __import__("engine.lifecycle", fromlist=["new_lifecycle"])
        .new_lifecycle(T0, {"timeframe": "1h_close",
                            "operator": "price_at_or_above",
                            "level": "70000"}, "eth_haiku_raw-L1")),
    "thesis": lambda a, m: a["sol_haiku_ta"]["theses"].append(
        {"t": "x", "text": "leftover"}),
    "decisions": lambda a, m: a["btc_opus_raw"].update(n_decisions=1),
    "boundary": lambda a, m: m.update(boundary=T0),
    "history": lambda a, m: m.update(
        equity_history={"btc_haiku_raw": [{"T": T0, "equity": "10000.00"}]}),
}


@pytest.mark.parametrize("case", sorted(DIRTY_CASES))
def test_dirty_state_refused_before_any_schedule(case):
    store = _dirty(DIRTY_CASES[case])
    eng, site = digests()
    with pytest.raises(official.PristineError):
        official.provision_official(store, eng, site, T0, total=2)
    # refused BEFORE the schedule / binding / publication existed
    assert not os.path.exists(os.path.join(store, pilot.SCHEDULE_NAME))
    assert not os.path.exists(os.path.join(store, official.BINDING_NAME))
    assert not os.path.exists(os.path.join(store, publisher.PUB_LOG))


def test_missing_state_creates_standard_pristine_state():
    store = tempfile.mkdtemp(prefix="arena-r15-")
    eng, site = digests()
    sched = official.provision_official(store, eng, site, T0, total=2)
    assert sched["start"] == T0
    accounts, meta = persistence.load_state(
        os.path.join(store, "state.json"), expect_full_roster=True)
    assert all(str(a["E"]) == "10000.00" and a["qty"] == 0
               for a in accounts.values())
    official.verify_pristine_official_state(store)   # round-trips clean


def test_started_schedule_uses_restart_path_not_pristine(cfg):
    """After a completed boundary the state is legitimately non-pristine;
    re-provisioning (restart) must NOT demand pristine accounts."""
    store = provisioned_official(n=2)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    caller = ScriptedCaller({"sol_haiku_raw": long_decision(
        float(_sol_price()), 2000)})
    with pytest.raises(recovery.CrashError):
        run_official(store, cfg, caller, dp, vc, crash_at="after_finalize")
    eng, site = digests()
    sched = official.provision_official(store, eng, site, T0, total=2)
    assert sched["boundaries"][0] == T0              # same sealed schedule


# ---- 015.2 disarm survives stop/reboot ----

def _record(tmp_path, start, name="act.json"):
    eng, site = digests()
    p = tmp_path / name
    p.write_text(json.dumps({
        "approved": "YES-OFFICIAL-RUN-APPROVED", "engine_digest": eng,
        "site_digest": site, "start_utc": start, "total": 2}))
    return str(p), official.activation_sha(str(p))


def test_full_stop_delete_restart_rearm_with_new_start(cfg, tmp_path):
    """The mentor's exact sequence: provision -> zero boundaries -> service
    stops -> record deleted -> restart reconciles to ARMED/OFF -> owner
    re-arms with a DIFFERENT start -> the NEW schedule is provisioned and
    used, with zero calls for the old one."""
    eng, site = digests()
    store = tempfile.mkdtemp(prefix="arena-r15-")
    act_path, sha_a = _record(tmp_path, T0)
    official.provision_official(store, eng, site, T0, total=2,
                                activation_sha=sha_a)
    assert official.reconcile_unstarted_schedule(store, act_path) == "match"
    # --- service stops; owner deletes the record; service restarts ---
    os.remove(act_path)
    assert official.reconcile_unstarted_schedule(store, act_path) \
        == "rolled_back"
    with pytest.raises(pilot.ScheduleError):
        pilot.load_schedule(store)                   # stale schedule gone
    assert not os.path.exists(os.path.join(store, official.BINDING_NAME))
    official.verify_pristine_official_state(store)   # accounts untouched
    # --- owner re-arms with a DIFFERENT start ---
    new_start = T0 + 5 * HOUR
    act_path2, sha_b = _record(tmp_path, new_start, "act2.json")
    assert official.reconcile_unstarted_schedule(store, act_path2) \
        == "no_schedule"
    sched = official.provision_official(store, eng, site, new_start, total=2,
                                        activation_sha=sha_b)
    assert sched["start"] == new_start
    vc = PilotClock(float(new_start) - 60)
    dp, _ = direct(vc)
    caller = ScriptedCaller({})
    sched = run_official(store, cfg, caller, dp, vc,
                         disarm_check=official.make_disarm_check(act_path2,
                                                                 sha_b))
    assert sched["completed"] == [new_start, new_start + HOUR]
    assert len(caller.calls) == 36                   # exactly the new run
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    for e in ledger:                                 # zero OLD-schedule calls
        assert str(T0) not in e["round_id"]


def test_changed_record_rolls_back_unstarted_schedule(tmp_path):
    eng, site = digests()
    store = tempfile.mkdtemp(prefix="arena-r15-")
    act_path, sha_a = _record(tmp_path, T0)
    official.provision_official(store, eng, site, T0, total=2,
                                activation_sha=sha_a)
    _record(tmp_path, T0 + HOUR, "act.json")         # same file, new content
    assert official.reconcile_unstarted_schedule(store, act_path) \
        == "rolled_back"


def test_unbound_schedule_rolls_back(tmp_path):
    """A schedule without a binding (or bound to a different SHA) can never
    silently survive: safety rolls it back."""
    eng, site = digests()
    store = tempfile.mkdtemp(prefix="arena-r15-")
    act_path, _ = _record(tmp_path, T0)
    official.provision_official(store, eng, site, T0, total=2)  # no sha bound
    assert official.reconcile_unstarted_schedule(store, act_path) \
        == "rolled_back"


def test_started_schedule_is_immutable_after_stop(cfg, tmp_path):
    eng, site = digests()
    store = provisioned_official(n=2)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    with pytest.raises(recovery.CrashError):
        run_official(store, cfg, ScriptedCaller({}), dp, vc,
                     crash_at="after_one_pair")      # boundary 1 STARTED
    act_path = str(tmp_path / "gone.json")           # record never existed
    assert official.reconcile_unstarted_schedule(store, act_path) == "started"
    sched = pilot.load_schedule(store)
    assert sched["boundaries"][0] == T0              # schedule preserved


def test_runner_script_reconciles_at_startup():
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    assert src.count("reconcile_unstarted_schedule") >= 2   # boot + per-arm
    assert "activation_sha=act_sha" in src           # binding is written


# ---- 015.3 transactional resolution: nothing commits after T+630 ----

def _prod_store():
    store = tempfile.mkdtemp(prefix="arena-r15tx-")
    persistence.save_state(store + "/state.json", state.init_accounts(),
                           {"boundary": None})
    config.write_launch_manifest(store)
    return store


def _assert_untouched(store, aid):
    accounts, _ = persistence.load_state(store + "/state.json")
    a = accounts[aid]
    assert str(a["E"]) == "10000.00" and a["qty"] == 0
    assert a["trades"] == [] and str(a["fees_total"]) == "0"
    assert a["lifecycle"] is None and a["lifecycles"] == []


def test_slow_account_execution_discards_prepared_commit(cfg, snapshots,
                                                         monkeypatch):
    """The mentor's demonstrated hole: resolution BEGINS before T+630 but the
    execution work finishes after. The prepared result must be discarded —
    PAIR_ABORTED, zero account mutation, zero trades, no executed links."""
    from engine import rounds as rounds_mod
    store = _prod_store()
    clock = ManualClock(600.0)                       # resolution begins <630
    real_commit = rounds_mod._commit_account

    def slow_commit(acct, dec, snap, T):
        clock.t += 100.0                             # crosses 630 mid-work
        return real_commit(acct, dec, snap, T)
    monkeypatch.setattr(recovery.rounds, "_commit_account", slow_commit)
    d = long_decision(float(_sol_price()), 2000)
    ledger, _, _ = recovery.run_checkpointed(
        T0, snapshots, ScriptedCaller({"sol_haiku_raw": d}), cfg, store,
        clock=clock, deadline=100000.0, resolution_deadline=630.0)
    pairs = [e for e in ledger if e.get("status")]
    assert len(pairs) == 9
    assert {e["status"] for e in pairs} == {"PAIR_ABORTED"}
    assert {e["reason"] for e in pairs} == {"deadline_exceeded"}
    _assert_untouched(store, "sol_haiku_raw")        # discarded WHOLE
    _assert_untouched(store, "sol_haiku_ta")
    led = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    assert not [e for e in led if e.get("e") == "executed_attempt"]
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["boundary_complete"] is True         # still terminal


def test_slow_preparation_serialization_discards_commit(cfg, snapshots,
                                                        monkeypatch):
    """Slow state-copy/serialization preparation (not the model, not the
    execution math) must also be caught by the post-preparation clock gate."""
    import copy as copy_mod
    store = _prod_store()
    clock = ManualClock(600.0)
    real_deepcopy = copy_mod.deepcopy

    def slow_deepcopy(obj, *a, **k):
        clock.t += 400.0                             # far past 630
        return real_deepcopy(obj, *a, **k)
    monkeypatch.setattr(recovery.copy, "deepcopy", slow_deepcopy)
    d = long_decision(float(_sol_price()), 2000)
    ledger, _, _ = recovery.run_checkpointed(
        T0, snapshots, ScriptedCaller({"sol_haiku_raw": d}), cfg, store,
        clock=clock, deadline=100000.0, resolution_deadline=630.0)
    pairs = [e for e in ledger if e.get("status")]
    assert {e["status"] for e in pairs} == {"PAIR_ABORTED"}
    _assert_untouched(store, "sol_haiku_raw")


def test_slow_replay_candle_is_discarded_whole(cfg, snapshots, monkeypatch):
    from conftest import load_fix
    from engine import marketdata
    store = _prod_store()
    candles = {c: [x for x in marketdata.to_dec(load_fix(c, "1m"))
                   if T0 <= x["t"] < T0 + HOUR] for c in ("BTC", "ETH", "SOL")}
    spec = {c: {"start": T0, "end": T0 + HOUR, "candles": cs}
            for c, cs in candles.items()}
    clock = ManualClock(600.0)                       # replay begins <630
    real_replay = recovery.replay_mod.replay

    def slow_replay(accts, cs, recs):
        clock.t += 200.0                             # crosses 630 mid-candle
        return real_replay(accts, cs, recs)
    monkeypatch.setattr(recovery.replay_mod, "replay", slow_replay)
    ledger, _, _ = recovery.run_checkpointed(
        T0, snapshots, ScriptedCaller({}), cfg, store, clock=clock,
        deadline=100000.0, replay_spec=spec, replay_deadline=630.0)
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["boundary_complete"] is True
    catchups = [e for e in ledger if e.get("replay")
                and e["replay"][0]["e"] == "CATCHUP_REQUIRED"]
    assert catchups                                  # declared safe behavior
    for coin in ("BTC", "ETH", "SOL"):
        rs = meta["replay_state"].get(coin, {})
        if rs.get("status") == "CATCHUP_REQUIRED":
            # the half-done candle was discarded: watermark never advanced
            # into or past the latched gap
            wm = meta["replay_watermark"].get(coin)
            assert wm is None or wm < rs["gap_since"]


# ---- 015.4 preflight must not false-pass ----

def _preflight(cfg, snapshots, call_fn):
    mod = _load_script("preflight_official")
    store = tempfile.mkdtemp(prefix="arena-pf15-")
    return mod.run_preflight(store, cfg, snapshots, call_fn, T0)


def _mk_call(cfg, decision_for=None, model_for=None):
    def call_fn(aid, system, user):
        want = cfg["models"][aid.split("_")[1]]["model"]
        dec = (decision_for or (lambda a: flat_decision()))(aid)
        rmodel = (model_for or (lambda a, w: w))(aid, want)
        return dec, rmodel, "tool_use", 0.1, want
    return call_fn


def test_all_semantically_invalid_is_fail(cfg, snapshots):
    bad = dict(long_decision(50000, 2000), invalidation=None)   # sem-invalid
    summary, _, _ = _preflight(cfg, snapshots,
                               _mk_call(cfg, lambda a: dict(bad)))
    assert summary["accepted"] == 18                 # the old false-pass path
    assert summary["semantically_valid_first_try"] == 0
    assert summary["model_calls_pass"] is False      # now correctly FAIL


def test_one_semantically_invalid_is_fail(cfg, snapshots):
    bad = dict(long_decision(50000, 2000), invalidation=None)

    def decide(aid):
        return dict(bad) if aid == "btc_haiku_raw" else flat_decision()
    summary, _, _ = _preflight(cfg, snapshots, _mk_call(cfg, decide))
    assert summary["semantically_valid_first_try"] == 17
    assert summary["model_calls_pass"] is False


def test_wrong_or_fuzzy_model_identity_is_fail(cfg, snapshots):
    summary, _, _ = _preflight(
        cfg, snapshots,
        _mk_call(cfg, model_for=lambda a, w: w + "-20991231"
                 if a == "eth_opus_ta" else w))
    assert summary["identity_ok"] == 17              # prefix match REJECTED
    assert summary["model_calls_pass"] is False
    assert "EXACTLY equal" in summary["identity_rule"]


def test_all_valid_passes_and_makes_exactly_18_calls(cfg, snapshots):
    calls = []

    def call_fn(aid, system, user):
        calls.append(aid)
        want = cfg["models"][aid.split("_")[1]]["model"]
        return flat_decision(), want, "tool_use", 0.1, want
    summary, _, _ = _preflight(cfg, snapshots, call_fn)
    assert summary["model_calls_pass"] is True
    assert len(calls) == 18 and len(set(calls)) == 18


def test_preflight_peak_concurrency_matches_production_limit(cfg, snapshots):
    limit = cfg["collection"]["concurrency_max_simultaneous_requests"]
    assert limit == 6
    barrier = threading.Barrier(limit, timeout=10)
    lock = threading.Lock()
    stats = {"in": 0, "peak": 0, "n": 0}

    def call_fn(aid, system, user):
        with lock:
            stats["in"] += 1
            stats["n"] += 1
            stats["peak"] = max(stats["peak"], stats["in"])
        barrier.wait()                               # 6 must run TOGETHER
        with lock:
            stats["in"] -= 1
        want = cfg["models"][aid.split("_")[1]]["model"]
        return flat_decision(), want, "tool_use", 0.1, want
    summary, _, _ = _preflight(cfg, snapshots, call_fn)
    assert stats["peak"] == limit                    # production concurrency
    assert stats["n"] == 18                          # still exactly 18
    assert summary["model_calls_pass"] is True


# ---- 015.5 hardened deployment verifier ----

GOOD_NGINX = open(os.path.join(config.ROOT, "deploy",
                               "nginx-arena.conf")).read()


def _fails(checks):
    return [name for name, ok, _ in checks if not ok]


def test_verifier_accepts_the_audited_config():
    mod = _load_script("verify_deployment")
    assert _fails(mod.analyze_nginx(GOOD_NGINX)) == []


def test_verifier_rejects_proxy_and_extra_locations():
    mod = _load_script("verify_deployment")
    evil = GOOD_NGINX.replace(
        "    location / { return 404; }",
        "    location / { return 404; }\n"
        "    location /admin { proxy_pass http://127.0.0.1:9000; }")
    fails = _fails(mod.analyze_nginx(evil))
    assert any("proxy_pass" in f for f in fails)
    assert any("unexpected location" in f for f in fails)


def test_verifier_rejects_writable_root_location():
    mod = _load_script("verify_deployment")
    evil = GOOD_NGINX.replace("location / { return 404; }",
                              "location / { autoindex on; }")
    fails = _fails(mod.analyze_nginx(evil))
    assert any("returns 404" in f for f in fails)


def test_verifier_rejects_missing_guard_headers():
    mod = _load_script("verify_deployment")
    evil = GOOD_NGINX.replace("        limit_except GET { deny all; }\n", "")
    fails = _fails(mod.analyze_nginx(evil))
    assert any("limit_except" in f for f in fails)


def test_verifier_https_and_redirect_checks():
    mod = _load_script("verify_deployment")

    def good_fetch(url):
        if url.startswith("https://"):
            return 200, {}
        return 301, {"Location": f"https://{mod.HOSTNAME}/health.json"}
    assert _fails(mod.check_https(good_fetch)) == []

    def no_redirect(url):
        return 200, {}
    fails = _fails(mod.check_https(no_redirect))
    assert any("redirect" in f for f in fails)


def test_docs_no_stale_restart_or_placeholder():
    for rel in ("deploy/DEPLOYMENT.md", "docs/handoff/BUILD_BATCH_DESIGN.md",
                "docs/handoff/NEXT_STEPS.md",
                "docs/handoff/OPERATIONS_RUNBOOK.md"):
        text = open(os.path.join(config.ROOT, rel)).read()
        assert "Restart=always" not in text, rel
        assert "ARENA_VPS_HOST" not in text, rel
