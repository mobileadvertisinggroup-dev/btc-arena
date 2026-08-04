"""Mentor Ruling 016 remediation regressions: causal live event order
(pre-decision replay), whole-store launch allowlist, durability-transactional
T+630/T+720, audited production response envelope, reconciled configuration,
certbot-aware nginx verifier, preflight-bound activation, and cleanup items.
All offline and deterministic."""
import importlib.util
import json
import os
import tempfile
import time
from decimal import Decimal

import pytest

from conftest import T0, ScriptedCaller, flat_decision, load_fix, long_decision
from engine import (config, marketdata, official, persistence, pilot,
                    recovery, rounds, state)
from test_official import direct, provisioned_official, run_official
from test_ruling010 import PilotClock, digests
from test_ruling011 import _sol_price
from test_ruling014 import ManualClock

HOUR = 3600
T1 = T0 + HOUR


def _load_script(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(config.ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _prod_store():
    store = tempfile.mkdtemp(prefix="arena-r16-")
    persistence.save_state(store + "/state.json", state.init_accounts(),
                           {"boundary": None})
    config.write_launch_manifest(store)
    return store


def snapshots_at(T):
    return {c: marketdata.build_snapshot(c, load_fix(c, "1m"),
                                         load_fix(c, "1h"),
                                         load_fix(c, "1d"), T)
            for c in ("BTC", "ETH", "SOL")}


def mk_candles(t0, n, price, dip_to=None, rise_to=None, at=30):
    """n flat 1m candles from t0; optionally one dip/spike at index `at`."""
    p = Decimal(str(price))
    out = []
    for i in range(n):
        c = {"t": t0 + i * 60, "o": p, "h": p, "l": p, "c": p,
             "v": Decimal("1")}
        if i == at and dip_to is not None:
            c["l"] = Decimal(str(dip_to))
        if i == at and rise_to is not None:
            c["h"] = Decimal(str(rise_to))
        out.append(c)
    return out


def _open_sol_positions(cfg, decision, both_arms=True):
    """Boundary 1 at T0: open the scripted SOL position(s); returns store."""
    store = _prod_store()
    script = {"sol_haiku_raw": dict(decision)}
    if both_arms:
        script["sol_haiku_ta"] = dict(decision)
    recovery.run_checkpointed(T0, snapshots_at(T0), ScriptedCaller(script),
                              cfg, store)
    return store


def _boundary2(store, cfg, candles, caller=None, crash_at=None):
    spec = {"SOL": {"start": T0, "end": T1, "candles": candles}}
    return recovery.run_checkpointed(
        T1, snapshots_at(T1), caller or ScriptedCaller({}), cfg, store,
        pre_replay_spec=spec, crash_at=crash_at)


# ---- 016.1 causal live event order (CRITICAL) ----

def test_prior_hour_stop_hit_makes_T_prompt_flat(cfg):
    p = float(_sol_price())
    store = _open_sol_positions(cfg, long_decision(p, 2000))
    stop = p * 0.97
    _boundary2(store, cfg, mk_candles(T0, 60, p, dip_to=stop * 0.999))
    arch = persistence.read_prompt_archive(store, f"v1-ALL-{T1}")
    for aid in ("sol_haiku_raw", "sol_haiku_ta"):    # Raw AND TA corrected
        user = arch[aid]["user"]
        assert "flat — no position" in user          # stop applied BEFORE T
        assert "Closed trades (1" in user            # and visible in prompt
        assert "stop_loss" in user
    accounts, _ = persistence.load_state(store + "/state.json")
    tr = accounts["sol_haiku_raw"]["trades"][0]
    assert tr["reason"] == "stop_loss"
    assert tr["closed_ts"] < T1                      # exit belongs to pre-T


def test_prior_hour_take_profit_makes_T_prompt_flat(cfg):
    p = float(_sol_price())
    store = _open_sol_positions(cfg, long_decision(p, 2000))
    _boundary2(store, cfg, mk_candles(T0, 60, p, rise_to=p * 1.06))
    user = persistence.read_prompt_archive(
        store, f"v1-ALL-{T1}")["sol_haiku_raw"]["user"]
    assert "flat — no position" in user
    assert "take_profit" in user


def test_prior_hour_invalidation_latch_visible_in_T_prompt(cfg):
    p = float(_sol_price())
    d = dict(long_decision(p, 2000), stop_loss=None, take_profit=None,
             invalidation={"timeframe": "1m_intrabar",
                           "operator": "price_at_or_below",
                           "level": p * 0.98})
    store = _open_sol_positions(cfg, d, both_arms=False)
    hold = dict(long_decision(p, 2000), stop_loss=None, take_profit=None,
                invalidation=None)                   # post-trigger: hold
    _boundary2(store, cfg, mk_candles(T0, 60, p, dip_to=p * 0.975),
               caller=ScriptedCaller({"sol_haiku_raw": hold}))
    user = persistence.read_prompt_archive(
        store, f"v1-ALL-{T1}")["sol_haiku_raw"]["user"]
    assert "Status: TRIGGERED" in user               # latch visible at T
    accounts, _ = persistence.load_state(store + "/state.json")
    a = accounts["sol_haiku_raw"]
    assert a["qty"] != 0                             # position still open
    assert a["lifecycle"]["triggered"] is not None   # latch is permanent
    assert a["lifecycle"]["post_trigger_action"] in ("held", "reduced",
                                                     "increased")


def test_T_action_never_touched_by_pre_T_candle(cfg):
    """The mentor's reproduced hole: pre-T stop exit + new T decision. The
    exit must settle the OLD position only, and the T action opens a FRESH
    lifecycle starting exactly at T — increasing at T can never enlarge an
    exit that happened before T."""
    p = float(_sol_price())
    store = _open_sol_positions(cfg, long_decision(p, 2000),
                                both_arms=False)
    accounts0, _ = persistence.load_state(store + "/state.json")
    old_qty = accounts0["sol_haiku_raw"]["qty"]
    p1 = float(snapshots_at(T1)["SOL"]["P_T"])
    reopen = long_decision(p1, 4000)                 # bigger position at T
    ledger, _, _ = _boundary2(
        store, cfg, mk_candles(T0, 60, p, dip_to=p * 0.968),
        caller=ScriptedCaller({"sol_haiku_raw": reopen}))
    accounts, _ = persistence.load_state(store + "/state.json")
    a = accounts["sol_haiku_raw"]
    tr = a["trades"][0]
    assert tr["reason"] == "stop_loss" and tr["closed_ts"] < T1
    assert Decimal(tr["qty"]) == abs(old_qty)        # OLD size only
    assert a["lifecycle"]["start_t"] == T1           # fresh T lifecycle
    assert a["qty"] != 0
    pair = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert pair["status"] == "PAIR_COMMITTED"


def test_crash_between_pre_replay_and_prompts_is_idempotent(cfg):
    p = float(_sol_price())
    store = _open_sol_positions(cfg, long_decision(p, 2000),
                                both_arms=False)
    candles = mk_candles(T0, 60, p, dip_to=p * 0.968, at=45)
    with pytest.raises(recovery.CrashError):
        _boundary2(store, cfg, candles, crash_at="during_pre_replay")
    # no prompt archived yet => recovery resumes WITHOUT aborting the pairs
    recovery.recover(store)
    _, meta = persistence.load_state(store + "/state.json")
    assert not meta.get("_recovering")
    ledger, _, _ = _boundary2(store, cfg, candles)
    accounts, meta = persistence.load_state(store + "/state.json")
    assert len(accounts["sol_haiku_raw"]["trades"]) == 1   # never twice
    assert meta["boundary_complete"] is True
    pair = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert pair["status"] == "PAIR_COMMITTED"        # resumed, not aborted


def test_pre_replay_spec_must_be_strictly_pre_T(cfg):
    store = _prod_store()
    bad = {"SOL": {"start": T0, "end": T1 + 60,
                   "candles": mk_candles(T0, 61, 100)}}
    with pytest.raises(ValueError):
        recovery.run_checkpointed(T1, snapshots_at(T1), ScriptedCaller({}),
                                  cfg, store, pre_replay_spec=bad)


def test_incomplete_pre_replay_blocks_coin_with_zero_calls(cfg):
    """A gap in the pre-decision candles => CATCHUP_REQUIRED before prompts:
    the coin's pairs abort DATA_UNAVAILABLE with zero model calls."""
    p = float(_sol_price())
    store = _open_sol_positions(cfg, long_decision(p, 2000),
                                both_arms=False)
    candles = [c for i, c in enumerate(mk_candles(T0, 60, p)) if i != 30]
    caller = ScriptedCaller({})
    ledger, _, _ = _boundary2(store, cfg, candles, caller=caller)
    assert not any(c.startswith("sol") for c in
                   (x["id"] for x in caller.calls))  # zero SOL model calls
    sol = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert sol["status"] == "PAIR_ABORTED" and sol["reason"] == "DATA_UNAVAILABLE"


def test_official_runner_uses_pre_decision_replay(cfg, monkeypatch):
    seen = {}
    real = recovery.run_checkpointed

    def spy(T, snaps, caller, cfg_, store_, **kw):
        seen.update(kw)
        return real(T, snaps, caller, cfg_, store_, **kw)
    monkeypatch.setattr(recovery, "run_checkpointed", spy)
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    run_official(store, cfg, ScriptedCaller({}), dp, vc)
    assert seen.get("pre_replay_spec") is not None   # causal order
    assert seen.get("replay_spec") is None           # legacy path unused
    assert seen["hard_deadline"] == T0 + 720


# ---- 016.2 whole-store pristine allowlist ----

CONTAMINANTS = {
    "ledger": lambda s: open(os.path.join(s, "ledger.jsonl"), "w").write(
        '{"round_id": "v1-BTC-old", "status": "PAIR_COMMITTED"}\n'),
    "prompts": lambda s: os.makedirs(os.path.join(s, "prompts", "old")),
    "attempts": lambda s: os.makedirs(os.path.join(s, "attempts", "old")),
    "publish": lambda s: os.makedirs(os.path.join(s, "publish")),
    "publications": lambda s: open(
        os.path.join(s, "publications.json"), "w").write("{}"),
    "binding": lambda s: open(
        os.path.join(s, official.BINDING_NAME), "w").write("{}"),
    "tempfile": lambda s: open(os.path.join(s, "state.json.tmp"), "w").write(""),
    "unknown": lambda s: open(os.path.join(s, "junk.bin"), "w").write("x"),
}


@pytest.mark.parametrize("name", sorted(CONTAMINANTS))
def test_contaminated_store_refused_before_first_schedule(name):
    store = tempfile.mkdtemp(prefix="arena-r16c-")
    persistence.save_state(os.path.join(store, "state.json"),
                           state.init_accounts(), {"boundary": None})
    CONTAMINANTS[name](store)
    eng, site = digests()
    with pytest.raises(official.PristineError):
        official.provision_official(store, eng, site, T0, total=2)
    assert not os.path.exists(os.path.join(store, pilot.SCHEDULE_NAME))


# ---- 016.3 durability-transactional T+630 / T+720 ----

def _sol_pair_pristine(store):
    accounts, _ = persistence.load_state(store + "/state.json")
    for aid in ("sol_haiku_raw", "sol_haiku_ta"):
        a = accounts[aid]
        assert str(a["E"]) == "10000.00" and a["qty"] == 0
        assert a["trades"] == [] and str(a["fees_total"]) == "0"
        assert a["lifecycles"] == []


def _slow_stage_run(cfg, monkeypatch, delay_hook):
    """Common rig: SOL long pair; clock starts at 600 (< 630); `delay_hook`
    arms a slowdown of a specific durability-preparation stage."""
    store = _prod_store()
    clock = ManualClock(600.0)
    armed = {"on": False}
    delay_hook(monkeypatch, clock, armed)
    real_commit = rounds._commit_account

    def arming_commit(acct, dec, snap, T):
        armed["on"] = True                           # preparation has begun
        return real_commit(acct, dec, snap, T)
    monkeypatch.setattr(recovery.rounds, "_commit_account", arming_commit)
    d = long_decision(float(_sol_price()), 2000)
    ledger, _, _ = recovery.run_checkpointed(
        T0, snapshots_at(T0), ScriptedCaller({"sol_haiku_raw": d}), cfg,
        store, clock=clock, deadline=100000.0, resolution_deadline=630.0)
    sol = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert sol["status"] == "PAIR_ABORTED"
    assert sol["reason"] == "deadline_exceeded"
    _sol_pair_pristine(store)                        # nothing survived
    led = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    assert not [e for e in led if e.get("e") == "executed_attempt"
                and e.get("pair") == "sol_haiku"]
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["boundary_complete"] is True


def test_slow_serialization_discards_durable_commit(cfg, monkeypatch):
    def hook(monkeypatch, clock, armed):
        real = persistence._enc_all

        def slow_enc(accounts):
            if armed["on"]:
                clock.t += 200.0
            return real(accounts)
        monkeypatch.setattr(persistence, "_enc_all", slow_enc)
    _slow_stage_run(cfg, monkeypatch, hook)


def test_slow_fsync_before_replacement_discards_commit(cfg, monkeypatch):
    """The mentor's exact hole: persistence crosses the deadline AFTER the
    old post-preparation check. The clock is now read immediately before the
    atomic replacement, so the fsynced temp is discarded."""
    def hook(monkeypatch, clock, armed):
        real = os.fsync

        def slow_fsync(fd):
            if armed["on"]:
                clock.t += 200.0
            return real(fd)
        monkeypatch.setattr(persistence.os, "fsync", slow_fsync)
    _slow_stage_run(cfg, monkeypatch, hook)


def test_slow_outbox_preparation_discards_commit(cfg, monkeypatch):
    def hook(monkeypatch, clock, armed):
        real = persistence.outbox_add

        def slow_outbox(meta, event_id, payload):
            if armed["on"] and ":exec:" in event_id:
                clock.t += 200.0
            return real(meta, event_id, payload)
        monkeypatch.setattr(persistence, "outbox_add", slow_outbox)
        monkeypatch.setattr(recovery.persistence, "outbox_add", slow_outbox)
    _slow_stage_run(cfg, monkeypatch, hook)


def test_slow_replay_persistence_discards_candle(cfg, monkeypatch):
    """Replay candle whose durable write crosses the deadline: discarded
    whole; watermark preserved; CATCHUP latched."""
    p = float(_sol_price())
    store = _open_sol_positions(cfg, long_decision(p, 2000),
                                both_arms=False)
    clock = ManualClock(600.0)
    real = os.fsync
    armed = {"on": False}

    def slow_fsync(fd):
        if armed["on"]:
            clock.t += 200.0
        return real(fd)
    monkeypatch.setattr(persistence.os, "fsync", slow_fsync)
    armed["on"] = True
    spec = {"SOL": {"start": T0, "end": T1,
                    "candles": mk_candles(T0, 60, p, dip_to=p * 0.968)}}
    recovery.run_checkpointed(
        T1, snapshots_at(T1), ScriptedCaller({}), cfg, store, clock=clock,
        deadline=100000.0, pre_replay_spec=spec, replay_deadline=630.0)
    accounts, meta = persistence.load_state(store + "/state.json")
    assert accounts["sol_haiku_raw"]["trades"] == []     # stop NOT applied
    assert meta["replay_watermark"].get("SOL") is None   # nothing advanced
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"
    assert meta["boundary_complete"] is True


def test_late_boundary_completion_is_never_silent(cfg):
    """Completion persisting after the hard T+720 bound is still terminal
    but explicitly recorded (late_termination_at + ledger event)."""
    store = _prod_store()
    clock = ManualClock(0.0)

    def slow_caller(aid, system, user, retry):
        clock.t += 100.0                             # 18 calls => T+1800
        return flat_decision()
    ledger, _, _ = recovery.run_checkpointed(
        T0, snapshots_at(T0), slow_caller, cfg, store, clock=clock,
        deadline=100000.0, hard_deadline=720.0)
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["boundary_complete"] is True
    assert meta["late_termination_at"] >= 720.0
    late = [e for e in ledger if e.get("e") == "LATE_TERMINATION"]
    assert late and late[0]["hard_deadline"] == 720.0


# ---- 016.4 audited production response envelope ----

def _envelope_caller(cfg, mutate=None):
    def caller(aid, system, user, retry):
        want = cfg["models"][aid.split("_")[1]]["model"]
        env = {"decision": flat_decision(), "response_model": want,
               "response_id": f"msg_{aid}_{retry is not None}",
               "stop_reason": "tool_use", "latency_ms": 123,
               "token_usage": {"input_tokens": 900, "output_tokens": 80},
               "raw_response": json.dumps({"model": want})}
        if mutate:
            mutate(aid, env)
        return env
    return caller


def test_wrong_returned_model_never_executes_and_is_archived(cfg):
    store = _prod_store()

    def mutate(aid, env):
        if aid == "sol_haiku_raw":
            env["response_model"] = env["response_model"] + "-imposter"
    ledger, attempts, _ = recovery.run_checkpointed(
        T0, snapshots_at(T0), _envelope_caller(cfg, mutate), cfg, store)
    sol = [e for e in ledger if e.get("pair") == "sol_haiku"][0]
    assert sol["status"] == "PAIR_ABORTED"
    assert sol["reason"] == "identity_mismatch"      # never executed
    recs = [r for r in attempts if r["account_id"] == "sol_haiku_raw"]
    assert recs and all(r.get("identity_mismatch") is True for r in recs)
    assert all(r["returned_model"].endswith("-imposter") for r in recs)
    accounts, _ = persistence.load_state(store + "/state.json")
    assert accounts["sol_haiku_raw"]["n_decisions"] == 0


def test_actual_response_metadata_is_archived_verbatim(cfg):
    store = _prod_store()
    _, attempts, _ = recovery.run_checkpointed(
        T0, snapshots_at(T0), _envelope_caller(cfg), cfg, store)
    rec = [r for r in attempts if r["account_id"] == "btc_haiku_raw"][0]
    assert rec["returned_model"] == cfg["models"]["haiku"]["model"]
    assert rec["latency_ms"] == 123                  # measured, not assumed
    assert rec["token_usage"] == {"input_tokens": 900, "output_tokens": 80}
    assert rec["response_id"].startswith("msg_btc_haiku_raw")
    assert rec["stop_reason"] == "tool_use"
    disk = json.load(open(persistence.attempt_path(store, rec)))
    assert disk["latency_ms"] == 123 and disk["response_id"] == rec["response_id"]


def test_legacy_caller_metadata_is_null_never_fabricated(cfg):
    store = _prod_store()
    _, attempts, _ = recovery.run_checkpointed(
        T0, snapshots_at(T0), ScriptedCaller({}), cfg, store)
    rec = attempts[0]
    assert rec["returned_model"] is None             # unknown stays unknown
    assert rec["latency_ms"] is None
    assert rec["token_usage"] is None


def test_validation_retry_preserves_true_metadata(cfg):
    store = _prod_store()
    bad = dict(long_decision(float(_sol_price()), 2000), invalidation=None)

    def mutate(aid, env):
        if aid == "sol_haiku_raw" and "True" not in env["response_id"]:
            env["decision"] = dict(bad)              # first try: sem-invalid
    _, attempts, _ = recovery.run_checkpointed(
        T0, snapshots_at(T0), _envelope_caller(cfg, mutate), cfg, store)
    recs = sorted((r for r in attempts if r["account_id"] == "sol_haiku_raw"),
                  key=lambda r: r["attempt_number"])
    assert len(recs) == 2                            # initial + retry
    assert recs[0]["semantic_validation_result"] == "invalid"
    assert recs[1]["semantic_validation_result"] == "valid"
    assert recs[0]["response_id"] != recs[1]["response_id"]  # true per-attempt


def test_retry_conversation_matches_frozen_config():
    desc = config.load_config()["request_payloads"]["common"]["messages"]
    assert "SECOND user message" in desc             # reconciled description
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    assert 'messages.append({"role": "user", "content": retry_msg})' in src
    assert "retry_message" in open(
        os.path.join(config.ROOT, "engine", "rounds.py")).read()


# ---- 016.5 configuration reconciliation contract tests ----

def test_config_scheduler_matches_official_implementation(cfg):
    s = cfg["scheduler"]
    assert s["mode"] == "vps_systemd"
    assert "arena-official.service" in s["service_unit"]
    assert os.path.exists(os.path.join(config.ROOT,
                                       "deploy/arena-official.service"))
    assert "T+720" in s["boundary_anchor"].replace("720s", "720")
    for n in ("510", "630", "690"):
        assert n in s["boundary_anchor"]
    assert official.OFFICIAL_PUBLIC_ORIGIN.rstrip("/") in s["monitoring"] \
        or "live.akraarena.online" in s["monitoring"]
    assert "cron" not in json.dumps(s).lower()       # GitHub cron era gone


def test_config_event_order_and_paths_match_engine(cfg):
    vet = cfg["round_contract"]["virtual_event_time"]
    assert "strictly" in vet and "before T" in vet
    assert "PRE-DECISION" in vet.upper() or "pre-decision" in vet.lower()
    # archive paths describe the REAL layout
    rec = {"round_id": "v1-SOL-2026-08-11T00:00:00Z",
           "account_id": "sol_haiku_raw", "attempt_number": 1}
    real = persistence.attempt_path("data-v1", rec)
    assert real == ("data-v1/attempts/v1-SOL-2026-08-11T00_00_00Z/"
                    "sol_haiku_raw_attempt1.json")
    assert "replaced by '_'" in cfg["attempt_archive"]["path"]
    assert cfg["prompt_archive"] == \
        "data-v1/prompts/v1-ALL-{T}/{account_id}.json"
    for f in ("response_id", "stop_reason"):
        assert any(f in x for x in cfg["attempt_archive"]["fields"])
    assert "NOT yet authorized" not in cfg["classification"]
    assert "live.akraarena.online" in cfg["dashboard"]["publication_model"]
    assert "live.akraarena.online" in cfg["persistence"]["publication"]
    assert cfg["round_contract"]["duration"].count("336")


# ---- 016.6 hardened effective-nginx verifier ----

from test_ruling015 import GOOD_NGINX  # noqa: E402  (certbot two-block)


def _fails(checks):
    return [name for name, ok, _ in checks if not ok]


def test_standard_certbot_two_block_config_passes():
    mod = _load_script("verify_deployment")
    assert _fails(mod.analyze_nginx(GOOD_NGINX)) == []


def test_server_level_return_200_fails():
    mod = _load_script("verify_deployment")
    evil = GOOD_NGINX.replace("    root /var/www/arena;",
                              '    root /var/www/arena;\n'
                              '    return 200 "oops";')
    fails = _fails(mod.analyze_nginx(evil))
    assert any("server-level return" in f for f in fails)


def test_extra_rewrite_if_location_proxy_all_fail():
    mod = _load_script("verify_deployment")
    for evil_line, marker in (
            ("    rewrite ^/x /y last;", "no rewrite"),
            ('    if ($arg_x) { return 200 "y"; }', "no if-blocks"),
            ("    location /up { proxy_pass http://127.0.0.1:1; }",
             "proxy_pass"),
            ("    location /extra { try_files $uri =404; }",
             "unexpected location")):
        evil = GOOD_NGINX.replace("    root /var/www/arena;",
                                  "    root /var/www/arena;\n" + evil_line)
        fails = _fails(mod.analyze_nginx(evil))
        assert any(marker in f for f in fails), (evil_line, fails)


def test_http_block_must_only_redirect():
    mod = _load_script("verify_deployment")
    evil = GOOD_NGINX.replace("    return 404; # managed by Certbot",
                              "    return 404;\n"
                              "    location /sneak { autoindex on; }")
    fails = _fails(mod.analyze_nginx(evil))
    assert any("no location blocks in the HTTP" in f for f in fails)
    evil2 = GOOD_NGINX.replace(
        "return 301 https://$host$request_uri;",
        'return 200 "hijacked";')
    fails2 = _fails(mod.analyze_nginx(evil2))
    assert any("approved HTTPS redirect" in f for f in fails2)


def test_verifier_requires_both_digests(monkeypatch):
    mod = _load_script("verify_deployment")
    monkeypatch.setattr(mod.sys, "argv", ["verify_deployment.py"])
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert e.value.code == 2


# ---- 016.7 activation bound to a passing preflight ----

def _passing_report(tmp_path, eng, site, ts=None, **over):
    summary = {"overall_pass": True, "n": 18, "accepted": 18,
               "engine_digest": eng, "site_digest": site,
               "canonical_endpoint": official.OFFICIAL_PAYLOAD_URL,
               "timestamp": int(ts if ts is not None else time.time())}
    summary.update(over)
    p = tmp_path / "preflight_report.json"
    p.write_text(json.dumps({"summary": summary, "results": {}}))
    import hashlib
    return str(p), hashlib.sha256(p.read_bytes()).hexdigest()


def _act(path, sha, eng, site):
    return {"engine_digest": eng, "site_digest": site,
            "preflight": {"report_path": path, "report_sha256": sha}}


def test_valid_fresh_matching_attestation_passes(tmp_path):
    eng, site = digests()
    path, sha = _passing_report(tmp_path, eng, site)
    assert official.verify_preflight_attestation(
        _act(path, sha, eng, site), eng, site, time.time()) is True


@pytest.mark.parametrize("case", [
    "missing", "failed", "engine_mismatch", "endpoint_mismatch",
    "modified", "stale", "bad_counts"])
def test_attestation_refusals(tmp_path, case):
    eng, site = digests()
    now = time.time()
    if case == "missing":
        act = {"engine_digest": eng, "site_digest": site}
    elif case == "failed":
        p, sha = _passing_report(tmp_path, eng, site, overall_pass=False)
        act = _act(p, sha, eng, site)
    elif case == "engine_mismatch":
        p, sha = _passing_report(tmp_path, "f" * 64, site)
        act = _act(p, sha, eng, site)
    elif case == "endpoint_mismatch":
        p, sha = _passing_report(tmp_path, eng, site,
                                 canonical_endpoint="https://evil/x.js")
        act = _act(p, sha, eng, site)
    elif case == "modified":
        p, sha = _passing_report(tmp_path, eng, site)
        with open(p, "a") as f:
            f.write(" ")                             # tampered after arming
        act = _act(p, sha, eng, site)
    elif case == "stale":
        p, sha = _passing_report(
            tmp_path, eng, site,
            ts=now - official.PREFLIGHT_VALIDITY_S - 60)
        act = _act(p, sha, eng, site)
    else:
        p, sha = _passing_report(tmp_path, eng, site, n=17)
        act = _act(p, sha, eng, site)
    with pytest.raises(official.PreflightAttestationError):
        official.verify_preflight_attestation(act, eng, site, now)


def test_arm_official_requires_and_embeds_the_attestation(tmp_path,
                                                         monkeypatch):
    mod = _load_script("arm_official")
    monkeypatch.setattr(mod, "ACTIVATION", str(tmp_path / "act.json"))
    eng, site = digests()
    base = ["arm_official.py", "--confirm", "--engine-digest", eng,
            "--site-digest", site, "--start-utc", "next-hour"]
    monkeypatch.setattr(mod.sys, "argv", list(base))
    with pytest.raises(SystemExit) as e:
        mod.main()                                   # no preflight => refuse
    assert e.value.code == 2 and not os.path.exists(str(tmp_path / "act.json"))
    path, sha = _passing_report(tmp_path, eng, site)
    monkeypatch.setattr(mod.sys, "argv",
                        base + ["--preflight-report", path,
                                "--preflight-sha", sha])
    mod.main()
    act = json.load(open(str(tmp_path / "act.json")))
    assert act["preflight"]["report_sha256"] == sha  # attestation embedded


# ---- 016.8 cleanup ----

def test_no_stale_docstrings_or_placeholders():
    runner = open(os.path.join(config.ROOT, "scripts",
                               "run_official_14d.py")).read()
    assert "Restart=always" not in runner
    for rel in ("deploy/DEPLOYMENT.md", "scripts/run_official_14d.py",
                "docs/handoff/OPERATIONS_RUNBOOK.md"):
        assert "<vps-host>" not in open(os.path.join(config.ROOT, rel)).read()


def test_disarm_clears_public_payload(tmp_path):
    store = provisioned_official(n=1)
    pub = tempfile.mkdtemp(prefix="arena-pub16-")
    for name in ("live_payload.js", "live_payload.sha256"):
        open(os.path.join(pub, name), "w").write("stale READY payload")
    assert official.rollback_unstarted(store, public_dir=pub) is True
    assert not os.path.exists(os.path.join(pub, "live_payload.js"))
    assert not os.path.exists(os.path.join(pub, "live_payload.sha256"))


def test_mirror_failure_note_never_leaks_credentials():
    notes = []

    def bad_mirror(T):
        raise RuntimeError(
            "push failed: https://x-access-token:ghp_SECRET123@github.com/x")
    t = official._fire_mirror(bad_mirror, T0, notes)
    t.join(timeout=5)
    assert notes and "SECRET" not in notes[0] and "ghp_" not in notes[0]
    assert "failed" in notes[0]
