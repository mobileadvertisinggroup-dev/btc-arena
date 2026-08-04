"""Ruling 010: external approved-digest provisioning, internal-id validation,
public publication, and pilot restart/recovery — all offline.
(Updated for Ruling 011: provisioning takes engine + site digests; each
boundary publishes THINKING then ROUND_COMMITTED, gated by a READY publish.)"""
import json
import os
import shutil
import tempfile
import threading

import pytest

from conftest import T0, ScriptedCaller, load_fix
from engine import (config, marketdata, persistence, pilot, prompts,
                    publisher, recovery)

COINS = ("BTC", "ETH", "SOL")
K = {c: {tf: load_fix(c, tf) for tf in ("1m", "1h", "1d")} for c in COINS}
FAR_FUTURE = T0 + 1000 * 3600


def fake_fetch(coin, T, first):
    snap = marketdata.build_snapshot(coin, K[coin]["1m"], K[coin]["1h"],
                                     K[coin]["1d"], T)
    spec = {"start": T if first else T - 3600, "end": T,
            "candles": marketdata.to_dec(K[coin]["1m"])}
    return snap, spec


class FakePublisher:
    """Records exactly what was published. `ok_first` calls succeed, then
    `fail_lifecycles` members always fail, then `fail_times` calls fail,
    then everything succeeds."""

    def __init__(self, fail_times=0, ok_first=0, fail_lifecycles=None):
        self.published = []
        self.fail_times = fail_times
        self.ok_first = ok_first
        self.fail_lifecycles = fail_lifecycles or set()
        self._lock = threading.Lock()

    def __call__(self, text):
        with self._lock:
            lc = parse_payload(text).get("round_lifecycle")
            if self.ok_first > 0:
                self.ok_first -= 1
            elif lc in self.fail_lifecycles:
                raise RuntimeError("simulated publish transport failure")
            elif self.fail_times > 0:
                self.fail_times -= 1
                raise RuntimeError("simulated publish transport failure")
            self.published.append(text)
            return text

    def by_lifecycle(self, lifecycle):
        return [p for p in map(parse_payload, self.published)
                if p["round_lifecycle"] == lifecycle]


class PilotClock:
    """Virtual epoch clock: run_pilot's grace waits advance it via sleep();
    nothing else moves it, so collection deadlines are deterministic."""

    def __init__(self, t=float(T0)):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def digests():
    return (config.build_manifest()["combined"],
            config.build_site_manifest()["combined"])


def provisioned_pilot(n=12):
    store = tempfile.mkdtemp(prefix="arena-r10-")
    eng, site = digests()
    pilot.provision(store, eng, site, T0, total=n)
    return store


def parse_payload(text):
    return json.loads(text.strip()[text.index("=") + 1:].rstrip("; \n"))


def run(store, cfg, caller, pub, crash_at=None):
    vc = PilotClock()
    return pilot.run_pilot(store, cfg, caller, fake_fetch, pub,
                           clock=vc, sleep=vc.sleep, crash_at=crash_at)


# ---- 1. externally approved digests: pre-launch code cannot approve itself ----

def test_mutated_tree_before_first_store_creation_halts(tmp_path, monkeypatch):
    """Mutate a canonical file BEFORE store creation: integrity halt, zero
    model calls, no state initialization or mutation. The approved digests
    are the PRISTINE tree's digests, supplied externally."""
    eng, site = digests()                            # mentor-approved digests
    tree = tmp_path / "tree"
    for d in ("engine", "scripts", "prompts", "schemas", "config", "docs",
              "deploy"):
        shutil.copytree(os.path.join(config.ROOT, d), tree / d)
    victim = tree / "prompts/v1/system.txt"
    victim.write_bytes(victim.read_bytes() + b"#pre-launch-mutation")
    monkeypatch.setattr(config, "ROOT", str(tree))
    store = tempfile.mkdtemp(prefix="arena-r10-mut-")
    caller = ScriptedCaller({})
    with pytest.raises(config.IntegrityError):
        pilot.provision(store, eng, site, T0)
    assert caller.calls == []                        # zero model calls
    assert os.listdir(store) == []                   # nothing initialized
    with pytest.raises(pilot.ScheduleError):
        pilot.load_schedule(store)


def test_mutated_site_before_store_creation_halts(tmp_path, monkeypatch):
    """Ruling 011.1: a mutated STATIC UI file likewise blocks provisioning."""
    eng, site = digests()
    tree = tmp_path / "tree"
    for d in ("engine", "scripts", "prompts", "schemas", "config", "docs",
              "deploy"):
        shutil.copytree(os.path.join(config.ROOT, d), tree / d)
    victim = tree / "docs/index.html"
    victim.write_bytes(victim.read_bytes() + b"<!--mutated-->")
    monkeypatch.setattr(config, "ROOT", str(tree))
    store = tempfile.mkdtemp(prefix="arena-r10-smut-")
    with pytest.raises(config.IntegrityError):
        pilot.provision(store, eng, site, T0)
    assert os.listdir(store) == []


@pytest.mark.parametrize("bad", ["", None, "f" * 64, "not-a-digest"])
def test_provision_requires_exact_external_digest(bad):
    _, site = digests()
    store = tempfile.mkdtemp(prefix="arena-r10-bad-")
    with pytest.raises(config.IntegrityError):
        pilot.provision(store, bad, site, T0)
    assert os.listdir(store) == []


@pytest.mark.parametrize("bad", ["", None, "f" * 64])
def test_provision_requires_exact_site_digest(bad):
    eng, _ = digests()
    store = tempfile.mkdtemp(prefix="arena-r10-bads-")
    with pytest.raises(config.IntegrityError):
        pilot.provision(store, eng, bad, T0)
    assert os.listdir(store) == []


def test_provision_with_matching_digests_creates_verified_store(cfg):
    store = provisioned_pilot()
    m = config.load_launch_manifest(store)
    assert m["combined"] == config.build_manifest()["combined"]
    sm = config.load_site_manifest(store)
    assert sm["combined"] == config.build_site_manifest()["combined"]
    accounts, _ = persistence.load_state(os.path.join(store, "state.json"),
                                         expect_full_roster=True)
    assert len(accounts) == 18
    sched = pilot.load_schedule(store)
    assert sched["boundaries"] == [T0 + i * 3600 for i in range(12)]


# ---- 2. internal account id validated under a VALID checksum ----

def id_corruption_case(cfg, snapshots, mutate):
    store = provisioned_pilot()
    spath = os.path.join(store, "state.json")
    accounts, meta = persistence.load_state(spath)
    mutate(accounts)
    persistence.save_state(spath, accounts, meta)    # valid recomputed checksum
    before = open(spath, "rb").read()
    caller = ScriptedCaller({})
    with pytest.raises(persistence.StateCorruption):
        recovery.run_checkpointed(T0, snapshots, caller, cfg, store)
    assert caller.calls == []                        # zero caller invocations
    assert open(spath, "rb").read() == before        # zero mutation


def test_mismatched_internal_id_rejected(cfg, snapshots):
    def m(accounts):
        accounts["btc_haiku_raw"]["id"] = "btc_haiku_ta"
    id_corruption_case(cfg, snapshots, m)


def test_duplicate_internal_id_rejected(cfg, snapshots):
    store = provisioned_pilot()
    spath = os.path.join(store, "state.json")
    accounts, meta = persistence.load_state(spath)
    accounts["btc_haiku_raw"]["id"] = "btc_haiku_ta"
    # two records now share the internal id "btc_haiku_ta"
    persistence.save_state(spath, accounts, meta)
    with pytest.raises(persistence.StateCorruption, match="duplicate internal"):
        persistence.load_state(spath)
    caller = ScriptedCaller({})
    with pytest.raises(persistence.StateCorruption):
        recovery.run_checkpointed(T0, snapshots, caller, cfg, store)
    assert caller.calls == []


def test_missing_internal_id_rejected(cfg, snapshots):
    def m(accounts):
        del accounts["btc_haiku_raw"]["id"]
    id_corruption_case(cfg, snapshots, m)


# ---- 3. public publication: exactly-once, verified, decoupled from trading ----

def test_progress_advances_0_through_12_one_publication_each(cfg):
    store = provisioned_pilot()
    caller = ScriptedCaller({})
    pub = FakePublisher()
    sched = run(store, cfg, caller, pub)
    assert len(sched["completed"]) == 12
    committed = pub.by_lifecycle("ROUND_COMMITTED")
    assert len(committed) == 12                      # one per committed boundary
    for i, pl in enumerate(committed):
        assert pl["pilot_progress"] == {"done": i + 1, "total": 12}
        assert pl["published_boundary"] == T0 + i * 3600
        assert pl["mode"] == "PILOT_12H"
    log = publisher.read_log(store)
    assert all(log[f"{T0 + i * 3600}:committed"]["status"] == "PUBLISHED"
               for i in range(12))


def test_rerun_after_completion_publishes_nothing_new(cfg):
    store = provisioned_pilot(n=3)
    caller = ScriptedCaller({})
    pub = FakePublisher()
    run(store, cfg, caller, pub)
    n_calls = len(caller.calls)
    n_rounds = len(pub.by_lifecycle("ROUND_COMMITTED"))
    n_think = len(pub.by_lifecycle("THINKING"))
    run(store, cfg, caller, pub)                     # rerun: only READY re-verified
    assert len(caller.calls) == n_calls              # no re-trading
    assert len(pub.by_lifecycle("ROUND_COMMITTED")) == n_rounds
    assert len(pub.by_lifecycle("THINKING")) == n_think


def test_publication_failure_keeps_engine_state_and_marks_failed(cfg):
    store = provisioned_pilot(n=2)
    caller = ScriptedCaller({})
    pub = FakePublisher(fail_lifecycles={"ROUND_COMMITTED"})
    sched = run(store, cfg, caller, pub)
    assert len(sched["completed"]) == 2              # trading unaffected
    log = publisher.read_log(store)
    assert all(log[f"{T}:committed"]["status"] == "FAILED"
               for T in sched["completed"])
    accounts, meta = persistence.load_state(os.path.join(store, "state.json"),
                                            expect_full_roster=True)
    assert meta["boundary_complete"] is True         # committed state intact


def test_retry_republishes_only_no_model_calls_no_trades(cfg):
    store = provisioned_pilot(n=2)
    caller = ScriptedCaller({})
    failing = FakePublisher(fail_lifecycles={"ROUND_COMMITTED"})
    run(store, cfg, caller, failing)
    n_calls = len(caller.calls)
    spath = os.path.join(store, "state.json")
    lpath = os.path.join(store, "ledger.jsonl")
    state_before = open(spath, "rb").read()
    ledger_before = open(lpath, "rb").read()
    working = FakePublisher()
    results = publisher.reconcile(store, cfg, working)
    assert len(caller.calls) == n_calls              # zero new model calls
    assert open(spath, "rb").read() == state_before  # engine state untouched
    assert open(lpath, "rb").read() == ledger_before
    log = publisher.read_log(store)
    assert log[f"{T0 + 3600}:committed"]["status"] == "PUBLISHED"
    assert log[f"{T0}:committed"]["status"] == "SUPERSEDED"
    assert dict(results)[T0 + 3600] == "PUBLISHED"
    pl = parse_payload(working.published[-1])
    assert pl["pilot_progress"]["done"] == 2


def test_lying_publisher_fails_ready_gate_zero_model_calls(cfg):
    """A publisher that publishes the wrong payload fails verification at the
    READY gate => PublicationError BEFORE any model call (Ruling 011.3)."""
    store = provisioned_pilot(n=1)
    caller = ScriptedCaller({})

    def liar(text):
        return 'window.ARENA_LIVE = {"published_boundary": 0};\n'
    with pytest.raises(publisher.PublicationError):
        run(store, cfg, caller, liar)
    assert caller.calls == []                        # gate held: zero calls
    log = publisher.read_log(store)
    assert log["ready"]["status"] == "FAILED"
    assert "identifier" in log["ready"]["reason"]


# ---- 4. restart/recovery: same schedule, exactly N unique boundaries ----

@pytest.mark.parametrize("crash_point", ["after_prompts", "after_one_pair",
                                         "after_checkpoint", "during_replay"])
def test_crash_restart_resumes_same_schedule(cfg, crash_point):
    store = provisioned_pilot(n=3)
    sched0 = pilot.load_schedule(store)
    caller = ScriptedCaller({})
    pub = FakePublisher()
    with pytest.raises(recovery.CrashError):
        run(store, cfg, caller, pub, crash_at=crash_point)
    # RESTART: same store, no crash injection — must resume, not restart
    sched = run(store, cfg, ScriptedCaller({}), pub)
    assert sched["boundaries"] == sched0["boundaries"]   # schedule unchanged
    assert sorted(sched["completed"]) == sched0["boundaries"]
    assert len(set(sched["completed"])) == 3             # exactly 3 unique
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    rounds_seen = {e["round_id"] for e in ledger if e.get("status")}
    allowed = {prompts.round_id(c, T)
               for c in COINS for T in sched0["boundaries"]}
    assert rounds_seen <= allowed                        # no extra boundaries
    accounts, meta = persistence.load_state(os.path.join(store, "state.json"),
                                            expect_full_roster=True)
    assert meta["boundary_complete"] is True


def test_recovery_rule_applied_on_restart(cfg):
    """Crash mid-boundary; on restart the SAME boundary is finished under the
    frozen rule: non-finalized pairs abort as crash_recovery."""
    store = provisioned_pilot(n=1)
    caller = ScriptedCaller({})
    pub = FakePublisher()
    with pytest.raises(recovery.CrashError):
        run(store, cfg, caller, pub, crash_at="after_one_pair")
    n_calls = len(caller.calls)
    run(store, cfg, caller, pub)
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    reasons = {e.get("reason") for e in ledger if e.get("status")}
    assert "crash_recovery" in reasons                   # frozen rule applied
    assert len(caller.calls) == n_calls                  # no re-asking models
    sched = pilot.load_schedule(store)
    assert len(sched["completed"]) == 1


def test_restart_after_boundary_commit_before_mark_is_idempotent(cfg):
    """Crash in the window between the coordinator finishing a boundary and
    the schedule marking it complete: the rerun re-enters run_checkpointed for
    the same T, finds every pair finalized, and makes zero new model calls."""
    store = provisioned_pilot(n=1)
    caller = ScriptedCaller({})
    pub = FakePublisher()
    snaps, spec = {}, {}
    for coin in COINS:
        snaps[coin], spec[coin] = fake_fetch(coin, T0, True)
    recovery.run_checkpointed(T0, snaps, caller, cfg, store, replay_spec=spec)
    n_calls = len(caller.calls)                          # boundary committed,
    # ...but crash happened before mark_completed: schedule still empty
    assert pilot.load_schedule(store)["completed"] == []
    sched = run(store, cfg, caller, pub)
    assert len(caller.calls) == n_calls                  # zero duplicate calls
    assert sched["completed"] == [T0]
    assert len(pub.by_lifecycle("ROUND_COMMITTED")) == 1
