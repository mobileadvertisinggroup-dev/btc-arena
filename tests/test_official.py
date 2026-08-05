"""Official 14-day runner (post-pilot Mentor Rulings 1, 2, 3, 5, 6, 8):
direct VPS publication with checksum verification, explicit sub-budgets
inside the unchanged hard T+12:00 deadline, sealed 336-boundary UTC schedule,
one-runner lock, ARMED/OFF activation record, health, daily snapshots, and
the entry_t payload addition. All offline and deterministic."""
import importlib.util
import json
import os
import tarfile
import tempfile
import time

import pytest

from conftest import T0, ScriptedCaller, long_decision
from engine import (config, official, persistence, pilot, publisher,
                    recovery)
from test_ruling010 import PilotClock, digests, fake_fetch, parse_payload
from test_ruling011 import _sol_price

HOUR = 3600


def provisioned_official(n=2):
    store = tempfile.mkdtemp(prefix="arena-official-")
    eng, site = digests()
    official.provision_official(store, eng, site, T0, total=n)
    return store


class ServedDir:
    """Simulates the Nginx endpoint: serves exactly what is on disk in the
    public dir, optionally unreachable during a [down_from, down_until)
    virtual-time window, optionally tampering with the served bytes."""

    def __init__(self, public_dir, vc, down_from=None, down_until=None,
                 tamper=False):
        self.dir, self.vc = public_dir, vc
        self.down_from = down_from if down_from is not None else float("inf")
        self.down_until = down_until if down_until is not None else float("-inf")
        self.tamper = tamper

    def _read(self, name):
        if self.down_from <= self.vc() < self.down_until:
            raise RuntimeError("endpoint unreachable")
        with open(os.path.join(self.dir, name)) as f:
            text = f.read()
        return text + ("/*tampered*/" if self.tamper else "")

    def fetch(self):
        return self._read(official.DirectPublisher.PAYLOAD)

    def fetch_sha(self):
        return self._read(official.DirectPublisher.CHECKSUM)


def direct(vc, down_from=None, down_until=None, tamper=False, with_sha=True):
    pub_dir = tempfile.mkdtemp(prefix="arena-public-")
    served = ServedDir(pub_dir, vc, down_from=down_from,
                       down_until=down_until, tamper=tamper)
    dp = official.DirectPublisher(
        pub_dir, served.fetch, vc, vc.sleep,
        fetch_sha=served.fetch_sha if with_sha else None)
    return dp, pub_dir


def run_official(store, cfg, caller, dp, vc, **kw):
    return official.run_official(store, cfg, caller, fake_fetch, dp,
                                 vc, vc.sleep, **kw)


# ---- Ruling 3: sealed official schedule ----

def test_provision_rejects_non_utc_hour_start():
    store = tempfile.mkdtemp(prefix="arena-official-")
    eng, site = digests()
    with pytest.raises(pilot.ScheduleError):
        official.provision_official(store, eng, site, T0 + 17)
    assert not os.path.exists(os.path.join(store, pilot.SCHEDULE_NAME))


def test_official_schedule_is_sealed_336_hourly_utc():
    store = tempfile.mkdtemp(prefix="arena-official-")
    eng, site = digests()
    sched = official.provision_official(store, eng, site, T0)
    assert sched["total"] == 336 and len(sched["boundaries"]) == 336
    assert sched["boundaries"][0] == T0
    assert sched["boundaries"][-1] == T0 + 335 * HOUR
    assert all(b % HOUR == 0 for b in sched["boundaries"])
    # sealed: persisted before any model call, and idempotent on re-provision
    again = official.provision_official(store, eng, site, T0 + 999 * HOUR)
    assert again["boundaries"] == sched["boundaries"]


# ---- Ruling 1: direct publication, id + checksum verified ----

def test_direct_publisher_requires_a_deadline(cfg):
    vc = PilotClock()
    dp, _ = direct(vc)
    with pytest.raises(publisher.PublicationError):
        dp('window.ARENA_LIVE = {"publication_id": "x"};\n')


def test_direct_publisher_verifies_id_and_checksum(cfg):
    vc = PilotClock()
    dp, pub_dir = direct(vc)
    dp.deadline = vc() + 60
    text = f'window.ARENA_LIVE = {{"publication_id": "{T0}:READY:0"}};\n'
    assert dp(text) == text
    served = open(os.path.join(pub_dir, "live_payload.js")).read()
    assert served == text                       # atomic local write
    sha = open(os.path.join(pub_dir, "live_payload.sha256")).read().strip()
    import hashlib
    assert sha == hashlib.sha256(text.encode()).hexdigest()


def test_direct_publisher_tampered_endpoint_fails(cfg):
    vc = PilotClock()
    dp, _ = direct(vc, tamper=True)
    dp.deadline = vc() + 30
    with pytest.raises(publisher.PublicationError):
        dp(f'window.ARENA_LIVE = {{"publication_id": "{T0}:READY:0"}};\n')


def test_direct_publisher_success_at_or_after_deadline_is_failure(cfg):
    """Ruling 2 boundary discipline: verification succeeding AT the deadline
    does not count (mirrors the frozen T+720.0 rejection semantics)."""
    vc = PilotClock()
    dp, _ = direct(vc)
    dp.deadline = vc()                          # already expired
    with pytest.raises(publisher.PublicationError):
        dp(f'window.ARENA_LIVE = {{"publication_id": "{T0}:READY:0"}};\n')


# ---- Ruling 2: THINKING verified by T+01:00 or zero model calls ----

def test_thinking_not_verified_by_T60_aborts_with_zero_model_calls(cfg):
    """Endpoint down until T+120: THINKING misses its T+60 budget => the
    boundary becomes terminal with 9 PAIR_ABORTED(thinking_not_verified) and
    ZERO caller invocations; the final payload still publishes (T+690 budget)
    and the run continues — no halt, no replay."""
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 300)
    # endpoint healthy for the pre-T READY gate, down T-120 .. T+120
    dp, _ = direct(vc, down_from=float(T0 - 120), down_until=float(T0 + 120))
    caller = ScriptedCaller({})
    sched = run_official(store, cfg, caller, dp, vc)
    assert caller.calls == []                        # ZERO model calls
    assert sched["completed"] == [T0]                # terminal, not replayed
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    pairs = [e for e in ledger if e.get("status")]
    assert len(pairs) == 9
    assert {e["status"] for e in pairs} == {"PAIR_ABORTED"}
    assert {e["reason"] for e in pairs} == {official.ABORT_THINKING}
    log = publisher.read_log(store)
    assert log[f"{T0}:thinking"]["status"] == "FAILED"
    assert log[f"{T0}:committed"]["status"] == "PUBLISHED"
    # prompt archive holds honest not_called markers, no rendered prompts
    arch = persistence.read_prompt_archive(store, f"v1-ALL-{T0}")
    assert len(arch) == 18
    assert all(e.get("not_called") == official.ABORT_THINKING
               for e in arch.values())


def test_collection_deadline_passed_to_coordinator_is_T_plus_510(cfg,
                                                                 monkeypatch):
    seen = {}
    real = recovery.run_checkpointed

    def spy(T, snaps, caller, cfg_, store_, **kw):
        seen["deadline"] = kw.get("deadline")
        seen["abort_all_reason"] = kw.get("abort_all_reason")
        return real(T, snaps, caller, cfg_, store_, **kw)
    monkeypatch.setattr(recovery, "run_checkpointed", spy)
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 300)
    dp, _ = direct(vc)
    sched = run_official(store, cfg, ScriptedCaller({}), dp, vc)
    assert seen["deadline"] == T0 + official.COLLECTION_DEADLINE_S == T0 + 510
    assert seen["abort_all_reason"] is None
    assert sched["completed"] == [T0]
    assert official.HARD_DEADLINE_S == \
        cfg["collection"]["hard_terminal_deadline_seconds_after_T"] == 720


def test_committed_boundary_publishes_official_branding(cfg):
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 300)
    dp, pub_dir = direct(vc)
    caller = ScriptedCaller({"sol_haiku_raw": long_decision(
        float(_sol_price()), 2000)})
    run_official(store, cfg, caller, dp, vc)
    pl = parse_payload(open(os.path.join(pub_dir, "live_payload.js")).read())
    assert pl["mode"] == "OFFICIAL_14D"
    assert pl["banner"] == official.BANNER
    assert pl["round_lifecycle"] == "ROUND_COMMITTED"
    assert pl["publication_id"] == f"{T0}:ROUND_COMMITTED:1"
    # Ruling 5: entry_t on the open position, null on flat accounts
    rows = {a["id"]: a for a in pl["coins"]["SOL"]["accounts"]}
    assert rows["sol_haiku_raw"]["entry_t"] == T0
    assert rows["sol_opus_raw"]["entry_t"] is None


def test_crash_restart_resumes_same_schedule_exactly_once(cfg):
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 300)
    dp, _ = direct(vc)
    caller = ScriptedCaller({})
    with pytest.raises(recovery.CrashError):
        run_official(store, cfg, caller, dp, vc, crash_at="after_one_pair")
    sched0 = pilot.load_schedule(store)
    assert sched0["completed"] == []                 # boundary incomplete
    dp2, _ = direct(vc)
    sched = run_official(store, cfg, caller, dp2, vc)
    assert sched["boundaries"] == sched0["boundaries"]   # never new boundaries
    assert sched["completed"] == [T0]
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    pairs = [e for e in ledger if e.get("status")]
    assert len(pairs) == 9                           # exactly once each
    # frozen recovery rule: the crashed boundary's non-finalized pairs abort
    assert any(e["reason"] == "crash_recovery" for e in pairs
               if e["status"] == "PAIR_ABORTED")


# ---- Ruling 3: final boundary preserves positions, no forced closes ----

def test_final_boundary_preserves_open_positions(cfg):
    store = provisioned_official(n=2)
    vc = PilotClock(float(T0) - 300)
    dp, pub_dir = direct(vc)
    d = long_decision(float(_sol_price()), 2000)
    # boundary 2: hold the same long (invalidation must be null when holding)
    # => the position remains open through the FINAL boundary
    hold = dict(d, invalidation=None)
    caller = ScriptedCaller({"sol_haiku_raw": [d, hold]})
    sched = run_official(store, cfg, caller, dp, vc)
    assert len(sched["completed"]) == 2              # experiment over
    accounts, meta = persistence.load_state(
        os.path.join(store, "state.json"), expect_full_roster=True)
    a = accounts["sol_haiku_raw"]
    assert a["qty"] != 0                             # position PRESERVED
    assert a["trades"] == []                         # no forced closing trade
    # final equity is computed at the frozen final Kraken mark
    pl = parse_payload(open(os.path.join(pub_dir, "live_payload.js")).read())
    row = {x["id"]: x for x in pl["coins"]["SOL"]["accounts"]}["sol_haiku_raw"]
    assert row["mark"] == str(meta["marks"]["SOL"])
    assert row["equity"] is not None


# ---- Ruling 6: lock, health, ARMED/OFF record ----

def test_runner_lock_is_exclusive():
    path = os.path.join(tempfile.mkdtemp(prefix="arena-lock-"), "runner.lock")
    lock = official.acquire_runner_lock(path)
    with pytest.raises(official.LockError):
        official.acquire_runner_lock(path)
    lock.close()                                     # release
    official.acquire_runner_lock(path).close()


def test_health_written_through_run(cfg):
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 300)
    dp, _ = direct(vc)
    hdir = tempfile.mkdtemp(prefix="arena-health-")
    run_official(store, cfg, ScriptedCaller({}), dp, vc, health_dir=hdir)
    h = json.load(open(os.path.join(hdir, "health.json")))
    assert h["state"] == "COMPLETE"
    assert h["mode"] == "OFFICIAL_14D"
    assert h["boundaries_done"] == 1 and h["total"] == 1
    assert h["latest_terminal"] == T0
    assert h["latest_publication"]["status"] == "PUBLISHED"
    assert h["pid"] == os.getpid()


def _load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "run_official_14d",
        os.path.join(config.ROOT, "scripts", "run_official_14d.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_activation_record_validation(tmp_path, monkeypatch):
    mod = _load_runner_module()
    act = tmp_path / "official_activation.json"
    monkeypatch.setattr(mod, "ACTIVATION", str(act))
    assert mod.read_activation() is None             # absent => ARMED/OFF
    act.write_text("{not json")
    assert mod.read_activation() is None             # malformed => ARMED/OFF
    eng, site = digests()
    good = {"approved": "YES-OFFICIAL-RUN-APPROVED", "engine_digest": eng,
            "site_digest": site, "start_utc": T0, "total": 336,
            "preflight": {"report_path": "/srv/preflight_report.json",
                          "report_sha256": "ab" * 32}}
    for corrupt in ({"approved": "yes"}, {"start_utc": T0 + 17},
                    {"total": 12}, {"engine_digest": "short"},
                    {"preflight": None},             # Ruling 016.7
                    {"preflight": {"report_path": ""}}):
        act.write_text(json.dumps({**good, **corrupt}))
        assert mod.read_activation() is None         # every field enforced
    act.write_text(json.dumps(good))
    got = mod.read_activation()                      # (record, sha) from the
    assert got[0] == good                            # SAME bytes (020.2)
    import hashlib
    assert got[1] == hashlib.sha256(act.read_bytes()).hexdigest()


# ---- Ruling 1.5: the mirror can never gate or break trading ----

def test_mirror_failure_never_affects_the_run(cfg):
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 300)
    dp, _ = direct(vc)

    def bad_mirror(T):
        raise RuntimeError("github is down")
    sched = run_official(store, cfg, ScriptedCaller({}), dp, vc,
                         mirror=bad_mirror)
    time.sleep(0.05)                                 # let the thread die
    assert sched["completed"] == [T0]
    log = publisher.read_log(store)
    assert log[f"{T0}:committed"]["status"] == "PUBLISHED"


# ---- Ruling 8: sealed daily snapshots ----

def test_snapshot_store_seals_and_never_overwrites():
    store = provisioned_official(n=1)
    out = tempfile.mkdtemp(prefix="arena-snap-")
    tar_path, sha = official.snapshot_store(store, out, "day01-test")
    man = json.load(open(os.path.join(out, "day01-test.MANIFEST.sha256.json")))
    assert man["count"] == len(man["files"]) >= 3    # state + 2 manifests
    with tarfile.open(tar_path) as tar:
        names = tar.getnames()
    assert "day01-test.MANIFEST.sha256.json" in names
    assert any(n.endswith("state.json") for n in names)
    recorded = open(tar_path + ".sha256").read().split()[0]
    assert recorded == sha
    # immutable: a second call returns the SAME sealed artifact
    tar2, sha2 = official.snapshot_store(store, out, "day01-test")
    assert (tar2, sha2) == (tar_path, sha)


# ---- coordinator regression: abort_all_reason leaves normal path intact ----

def test_abort_all_reason_zero_calls_and_marks_frozen(cfg, snapshots):
    accounts = None
    store = tempfile.mkdtemp(prefix="arena-abortall-")
    from engine import state as state_mod
    accounts = state_mod.init_accounts()
    persistence.save_state(store + "/state.json", accounts, {"boundary": None})
    config.write_launch_manifest(store)
    caller = ScriptedCaller({})
    ledger, attempts, _ = recovery.run_checkpointed(
        T0, snapshots, caller, cfg, store,
        abort_all_reason="thinking_not_verified")
    assert caller.calls == [] and attempts == []
    pairs = [e for e in ledger if e.get("status")]
    assert len(pairs) == 9
    assert {e["reason"] for e in pairs} == {"thinking_not_verified"}
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["boundary_complete"] is True
    assert meta["marks"]["BTC"] is not None          # marks still frozen
