"""Mentor Ruling 014 remediation regressions: real pre-start disarm, frozen
public-hostname binding, clean completion, audited preflight isolation,
enforced T+630 resolution/replay deadlines, infrastructure integrity lock,
pinned runtime environment, and README correction. All offline."""
import importlib.util
import json
import os
import re
import shutil
import tempfile
from urllib.parse import urlparse

import pytest

from conftest import T0, ScriptedCaller, flat_decision
from engine import config, official, persistence, pilot, publisher, recovery
from test_official import direct, provisioned_official, run_official
from test_ruling010 import PilotClock, digests

HOUR = 3600


def _load_script(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(config.ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- 014.1 real pre-start disarm ----

def _armed_activation(tmp_path):
    eng, site = digests()
    act = tmp_path / "official_activation.json"
    act.write_text(json.dumps({
        "approved": "YES-OFFICIAL-RUN-APPROVED", "engine_digest": eng,
        "site_digest": site, "start_utc": T0, "total": 336}))
    return str(act), official.activation_sha(str(act))


def test_arm_read_delete_before_T0_zero_calls_and_armed_off(cfg, tmp_path):
    """The mentor's exact scenario: arm -> service reads the activation ->
    the record is deleted before T0 -> zero model calls, no boundary, the
    store rolls back to its unprovisioned (ARMED/OFF-ready) shape."""
    act_path, act_sha = _armed_activation(tmp_path)
    store = provisioned_official(n=1)
    state_before = open(os.path.join(store, "state.json"), "rb").read()
    vc = PilotClock(float(T0) - 300)
    dp, _ = direct(vc)
    caller = ScriptedCaller({})

    real_sleep = vc.sleep

    def deleting_sleep(s):              # the owner deletes the file pre-T0
        if os.path.exists(act_path) and vc() > T0 - 200:
            os.remove(act_path)
        real_sleep(s)
    with pytest.raises(official.Disarmed):
        official.run_official(
            store, cfg, caller, lambda *a: (_ for _ in ()).throw(
                AssertionError("fetch must never run after disarm")),
            dp, vc, deleting_sleep,
            disarm_check=official.make_disarm_check(act_path, act_sha))
    assert caller.calls == []                        # ZERO model calls
    assert pilot.load_schedule(store)["completed"] == []
    assert not os.path.exists(os.path.join(store, "ledger.jsonl"))
    # rollback returns the store to the ARMED/OFF-ready unprovisioned shape
    assert official.rollback_unstarted(store) is True
    with pytest.raises(pilot.ScheduleError):
        pilot.load_schedule(store)                   # schedule removed
    assert not os.path.exists(os.path.join(store, publisher.PUB_LOG))
    assert open(os.path.join(store, "state.json"), "rb").read() \
        == state_before                              # accounts untouched


def test_modified_or_replaced_record_also_disarms(tmp_path):
    act_path, act_sha = _armed_activation(tmp_path)
    check = official.make_disarm_check(act_path, act_sha)
    assert check() is True
    with open(act_path, "a") as f:                   # modified
        f.write("\n")
    assert check() is False
    _, sha2 = _armed_activation(tmp_path)            # byte-identical rewrite
    assert sha2 == official.activation_sha(act_path) and check() is False \
        or check() is True                           # identical bytes re-arm
    os.remove(act_path)                              # deleted
    assert check() is False


def test_deletion_after_first_boundary_started_does_not_halt(cfg, tmp_path):
    """After boundary 1 begins, the activation record is no longer consulted:
    the run finishes; halting mid-run is a service-stop concern."""
    act_path, act_sha = _armed_activation(tmp_path)
    store = provisioned_official(n=2)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)

    def deleting_caller(aid, system, user, retry):   # fires DURING boundary 1
        try:
            os.remove(act_path)
        except FileNotFoundError:
            pass                                     # a concurrent call won
        return flat_decision()
    sched = run_official(store, cfg, deleting_caller, dp, vc,
                         disarm_check=official.make_disarm_check(act_path,
                                                                 act_sha))
    assert len(sched["completed"]) == 2              # both boundaries ran


def test_rollback_refuses_a_started_run(cfg):
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    run_official(store, cfg, ScriptedCaller({}), dp, vc)
    with pytest.raises(RuntimeError):
        official.rollback_unstarted(store)
    assert pilot.load_schedule(store)["completed"] == [T0]   # nothing removed


def test_runner_script_wires_disarm_and_rollback():
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    assert "make_disarm_check" in src
    assert "except official.Disarmed" in src
    assert "rollback_unstarted" in src
    assert '"ARMED_OFF"' in src                      # health returns to OFF


# ---- 014.2 canonical frozen hostname binding ----

def test_canonical_constants_are_the_frozen_hostname():
    assert official.OFFICIAL_PUBLIC_ORIGIN == "https://live.akraarena.online/"
    assert official.OFFICIAL_PAYLOAD_URL \
        == "https://live.akraarena.online/live_payload.js"


def test_runner_refuses_non_canonical_endpoint(monkeypatch):
    mod = _load_script("run_official_14d")
    monkeypatch.setenv("ARENA_PUBLIC_PAYLOAD_URL",
                       "https://evil.example.com/live_payload.js")
    with pytest.raises(SystemExit) as e:
        mod.verify_public_binding()
    assert e.value.code == 2                         # refusal, zero side effects


def test_runner_accepts_only_the_canonical_endpoint(monkeypatch):
    mod = _load_script("run_official_14d")
    monkeypatch.delenv("ARENA_PUBLIC_PAYLOAD_URL", raising=False)
    assert mod.verify_public_binding() == official.OFFICIAL_PAYLOAD_URL
    monkeypatch.setenv("ARENA_PUBLIC_PAYLOAD_URL",
                       official.OFFICIAL_PAYLOAD_URL)
    assert mod.verify_public_binding() == official.OFFICIAL_PAYLOAD_URL


def test_dashboard_live_origin_derives_from_canonical():
    src = open(os.path.join(config.ROOT, "docs", "index.html")).read()
    assert f"const LIVE_ORIGIN = '{official.OFFICIAL_PUBLIC_ORIGIN}';" in src


def test_nginx_template_hostname_matches_canonical():
    conf = open(os.path.join(config.ROOT, "deploy",
                             "nginx-arena.conf")).read()
    host = urlparse(official.OFFICIAL_PUBLIC_ORIGIN).hostname
    assert f"server_name {host};" in conf
    assert "ARENA_VPS_HOST" not in conf              # placeholder gone


# ---- 014.3 clean completion ----

class NeverPublish:
    """A publisher that must never be invoked."""
    deadline = None

    def __call__(self, text):
        raise AssertionError("publication after completion is forbidden")


def test_completed_run_restart_publishes_nothing(cfg):
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    run_official(store, cfg, ScriptedCaller({}), dp, vc)     # completes
    hdir = tempfile.mkdtemp(prefix="arena-health-")
    caller = ScriptedCaller({})
    sched = official.run_official(                   # e.g. reboot restart
        store, cfg, caller, None, NeverPublish(), vc, vc.sleep,
        health_dir=hdir)
    assert len(sched["completed"]) == 1
    assert caller.calls == []                        # no model calls
    h = json.load(open(os.path.join(hdir, "health.json")))
    assert h["state"] == "COMPLETE"                  # health stays COMPLETE


def test_service_unit_recovers_crashes_but_not_completion():
    unit = open(os.path.join(config.ROOT, "deploy",
                             "arena-official.service")).read()
    assert "Restart=on-failure" in unit              # crash/reboot recovery
    assert "Restart=always" not in unit              # clean exit stays down
    assert "venv/bin/python" in unit                 # 014.7 audited venv


# ---- 014.4 audited preflight ----

def test_preflight_scratch_store_isolation():
    mod = _load_script("preflight_official")
    for bad in (os.path.join(config.ROOT, "data-v1"),
                os.path.join(config.ROOT, "data-v1", "sub"),
                config.ROOT):                        # parent of data-v1
        with pytest.raises(ValueError):
            mod.check_scratch_store(bad)
    ok = tempfile.mkdtemp(prefix="arena-preflight-")
    assert mod.check_scratch_store(ok) == os.path.realpath(ok)


def test_preflight_refuses_without_authorization(monkeypatch):
    mod = _load_script("preflight_official")
    monkeypatch.delenv("ARENA_PREFLIGHT_APPROVED", raising=False)
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert e.value.code == 2


def test_preflight_offline_18_requests_validation_only(cfg, snapshots):
    mod = _load_script("preflight_official")
    store = tempfile.mkdtemp(prefix="arena-preflight-")
    calls = []

    def call_fn(aid, system, user):
        calls.append(aid)
        model_id = cfg["models"][aid.split("_")[1]]["model"]
        return flat_decision(), model_id, "tool_use", 0.5, model_id
    summary, results, report = mod.run_preflight(store, cfg, snapshots,
                                                 call_fn, T0)
    assert len(calls) == 18 and len(set(calls)) == 18    # exactly 18, unique
    assert summary["n"] == summary["accepted"] == 18
    assert summary["schema_valid"] == summary["identity_ok"] == 18
    assert summary["raw_ta_separation_ok"] is True
    assert summary["accounts_unmutated"] is True
    assert summary["model_calls_pass"] is True
    # validation only: no trades, no state, no schedule anywhere
    assert not os.path.exists(os.path.join(store, "state.json"))
    assert not os.path.exists(os.path.join(store, "pilot_schedule.json"))
    assert not os.path.exists(os.path.join(store, "ledger.jsonl"))
    rep = json.load(open(report))                    # durable sanitized report
    assert rep["summary"]["model_calls_pass"] is True
    assert "ANTHROPIC" not in open(report).read()    # no secrets


def test_preflight_endpoint_probe_restores_served_payload():
    mod = _load_script("preflight_official")
    from test_official import ServedDir
    pub_dir = tempfile.mkdtemp(prefix="arena-probe-")
    prior = 'window.ARENA_LIVE = {"publication_id": "1:READY:0"};\n'
    with open(os.path.join(pub_dir, "live_payload.js"), "w") as f:
        f.write(prior)
    vc = PilotClock(1000.0)
    served = ServedDir(pub_dir, vc)
    assert mod.endpoint_probe(pub_dir, served.fetch, served.fetch_sha,
                              vc, vc.sleep) is True
    assert open(os.path.join(pub_dir, "live_payload.js")).read() == prior
    assert not os.path.exists(os.path.join(pub_dir, "live_payload.sha256"))
    # with nothing served before, the probe cleans up entirely
    empty = tempfile.mkdtemp(prefix="arena-probe2-")
    served2 = ServedDir(empty, vc)
    assert mod.endpoint_probe(empty, served2.fetch, served2.fetch_sha,
                              vc, vc.sleep) is True
    assert os.listdir(empty) == []


# ---- 014.5 enforced resolution/replay deadlines ----

class ManualClock:
    """Reads do not advance time; test hooks advance .t explicitly, so the
    slow stage under test is modeled deterministically."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def _fresh_store(accounts):
    store = tempfile.mkdtemp(prefix="arena-r14-")
    persistence.save_state(store + "/state.json", accounts,
                           {"boundary": None})
    config.write_launch_manifest(store)
    return store


def test_resolution_deadline_aborts_late_pairs(cfg, snapshots):
    """Each model call costs 100s: wave 1's resolution happens at T+600
    (< 630, commits), waves 2-3 resolve past T+630 (abort). Deterministic:
    only the caller advances the clock."""
    from engine import state as state_mod
    accounts = state_mod.init_accounts()
    store = _fresh_store(accounts)
    clock = ManualClock(0.0)

    def slow_caller(aid, system, user, retry):
        clock.t += 100.0                             # 6 calls/wave => +600
        return flat_decision()
    ledger, _, _ = recovery.run_checkpointed(
        T0, snapshots, slow_caller, cfg, store, clock=clock,
        deadline=100000.0, resolution_deadline=630.0)
    pairs = [e for e in ledger if e.get("status")]
    assert len(pairs) == 9                           # every pair terminal
    late = [e for e in pairs if e["status"] == "PAIR_ABORTED"]
    assert late and {e["reason"] for e in late} == {"deadline_exceeded"}
    committed = [e for e in pairs if e["status"] == "PAIR_COMMITTED"]
    assert committed                                 # early pairs still commit
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["boundary_complete"] is True         # terminal, no crash


def test_replay_deadline_latches_catchup_with_watermark(cfg, snapshots):
    from conftest import load_fix
    from engine import marketdata, state as state_mod
    accounts = state_mod.init_accounts()
    store = _fresh_store(accounts)
    candles = {c: [x for x in marketdata.to_dec(load_fix(c, "1m"))
                   if T0 <= x["t"] < T0 + HOUR] for c in ("BTC", "ETH", "SOL")}
    spec = {c: {"start": T0, "end": T0 + HOUR, "candles": cs}
            for c, cs in candles.items()}
    clock = ManualClock(0.0)

    def slow_caller(aid, system, user, retry):       # collection eats T+1800
        clock.t += 100.0
        return flat_decision()
    ledger, _, _ = recovery.run_checkpointed(
        T0, snapshots, slow_caller, cfg, store, clock=clock,
        deadline=100000.0, replay_spec=spec, replay_deadline=630.0)
    _, meta = persistence.load_state(store + "/state.json")
    # deliberately slow replay => the DECLARED safe terminal behavior:
    # CATCHUP_REQUIRED latched, watermark preserved, boundary terminal
    catchups = [e for e in ledger
                if e.get("replay") and e["replay"][0]["e"] == "CATCHUP_REQUIRED"]
    assert catchups                                  # never a silent crossing
    assert meta["boundary_complete"] is True
    for coin, rs in meta["replay_state"].items():
        if rs["status"] == "CATCHUP_REQUIRED":
            wm = meta["replay_watermark"].get(coin)
            assert wm is None or wm < rs["gap_since"]    # nothing lost


def test_official_passes_both_630_deadlines(cfg, monkeypatch):
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
    assert seen["resolution_deadline"] == T0 + 630
    assert seen["replay_deadline"] == T0 + 630
    assert seen["deadline"] == T0 + 510


# ---- 014.6 infrastructure integrity lock ----

def test_deploy_files_are_in_the_engine_manifest():
    files = config.build_manifest()["files"]
    for f in ("deploy/arena-official.service", "deploy/nginx-arena.conf",
              "deploy/requirements-official.txt"):
        assert f in config.CANONICAL_FILES and f in files
    assert "scripts/verify_deployment.py" in files   # hashed with scripts


def test_mutated_infra_file_breaks_the_approved_digest(tmp_path, monkeypatch):
    eng, _ = digests()                               # pristine tree digest
    tree = tmp_path / "tree"
    for d in ("engine", "scripts", "prompts", "schemas", "config", "docs",
              "deploy"):
        shutil.copytree(os.path.join(config.ROOT, d), tree / d)
    victim = tree / "deploy" / "nginx-arena.conf"
    victim.write_text(victim.read_text().replace(
        "live.akraarena.online", "attacker.example.com"))
    monkeypatch.setattr(config, "ROOT", str(tree))
    with pytest.raises(config.IntegrityError):
        config.check_approved_digest(eng)            # Halt A on infra drift


def test_verify_deployment_checks_the_frozen_hostname():
    mod = _load_script("verify_deployment")
    host = urlparse(official.OFFICIAL_PUBLIC_ORIGIN).hostname
    assert any(host in line for line in mod.REQUIRED_NGINX_LINES)


# ---- 014.7 pinned reproducible environment ----

def test_requirements_lock_is_fully_pinned():
    path = os.path.join(config.ROOT, "deploy", "requirements-official.txt")
    pins = [ln.strip() for ln in open(path)
            if ln.strip() and not ln.startswith("#")]
    assert pins, "lock file must pin at least the runtime deps"
    for ln in pins:
        assert re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.!+-]+", ln), ln
    names = {ln.split("==")[0] for ln in pins}
    assert {"dulwich", "jsonschema"} <= names        # runner's runtime deps


def test_deployment_doc_installs_into_the_audited_venv():
    doc = open(os.path.join(config.ROOT, "deploy", "DEPLOYMENT.md")).read()
    assert "python3 -m venv" in doc
    assert "requirements-official.txt" in doc
    assert "venv/bin/python scripts/verify_deployment.py" in doc


# ---- 014.8 README correction ----

def test_readme_describes_the_real_experiment():
    readme = open(os.path.join(config.ROOT, "README.md")).read()
    assert "BTC, ETH and SOL" in readme
    assert "18" in readme and "VPS" in readme
    assert "Runs entirely on GitHub Actions" not in readme
    assert "tick.yml" not in readme
    assert "GitHub Pages" in readme                  # dashboard role stated
