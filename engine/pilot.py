"""12-hour pilot orchestration (Ruling 010): externally-approved provisioning,
persisted schedule, wired crash recovery, post-commit publication.

Every I/O dependency is injected (market fetcher, model caller, publisher,
clock, sleep) so the complete production control flow is testable offline.
scripts/run_pilot_12h.py adds nothing but the guard and real transports.

Restart contract: the schedule (start boundary + all 12 hourly boundaries +
completed set) is persisted at provisioning. A restart resumes the SAME
schedule — it first recovers any incomplete persisted boundary via
recovery.recover() (frozen rule: non-finalized pairs abort as crash_recovery,
finalized pairs stand), finishes that same boundary, and only then advances.
A pilot therefore produces exactly `total` unique scheduled boundaries no
matter how many restarts occur."""
import json
import os

from . import (config as config_mod, persistence, publisher as publisher_mod,
               recovery, state)

HOUR = 3600
SCHEDULE_NAME = "pilot_schedule.json"


class ScheduleError(Exception):
    pass


def provision(store, approved_digest, start, total=12):
    """Create (or resume) a store ONLY if the current tree matches the
    EXTERNALLY supplied mentor-approved combined digest (Ruling 010.1).
    On mismatch nothing is written: no manifest, no state, no schedule.
    Idempotent on restart: existing state/schedule are preserved verbatim."""
    config_mod.check_approved_digest(approved_digest)   # halt BEFORE any write
    config_mod.provision_store(store, approved_digest)
    spath = os.path.join(store, "state.json")
    if not os.path.exists(spath):
        persistence.save_state(spath, state.init_accounts(), {"boundary": None})
    if not os.path.exists(os.path.join(store, SCHEDULE_NAME)):
        _write_schedule(store, {
            "start": start, "total": total,
            "boundaries": [start + i * HOUR for i in range(total)],
            "completed": []})
    return load_schedule(store)


def _write_schedule(store, sched):
    path = os.path.join(store, SCHEDULE_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sched, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_schedule(store):
    try:
        with open(os.path.join(store, SCHEDULE_NAME)) as f:
            s = json.load(f)
    except Exception as e:
        raise ScheduleError(f"pilot schedule unavailable: {e}")
    if len(s.get("boundaries", [])) != s.get("total") \
            or sorted(set(s["boundaries"])) != s["boundaries"] \
            or not set(s.get("completed", [])) <= set(s["boundaries"]):
        raise ScheduleError("pilot schedule malformed")
    return s


def mark_completed(store, T):
    s = load_schedule(store)
    if T not in s["completed"]:
        s["completed"].append(T)
        _write_schedule(store, s)
    return s


def run_pilot(store, cfg, caller, fetch_market, publish, clock, sleep,
              grace=120, crash_at=None):
    """Drive the persisted schedule's boundaries exactly once each.

    fetch_market(coin, T, first) -> (snapshot, replay_spec); an exception
    marks that coin DATA_UNAVAILABLE for the boundary. publish(text) -> the
    text as actually published (verified by engine.publisher). Publication
    failure is recorded and NEVER re-executes a round; startup reconciles
    failed publications (publication only) before trading resumes."""
    sched = load_schedule(store)
    publisher_mod.reconcile(store, cfg, publish)
    spath = os.path.join(store, "state.json")
    for T in sched["boundaries"]:
        if T in sched["completed"]:
            continue
        _, meta = persistence.load_state(spath, expect_full_roster=True)
        if meta.get("boundary") is not None and not meta.get("boundary_complete"):
            recovery.recover(store)      # finish/abort the SAME boundary first
        while clock() < T + grace:
            sleep(max(1, min(30, T + grace - clock())))
        snaps, spec = {}, {}
        first = not sched["completed"]
        for coin in ("BTC", "ETH", "SOL"):
            try:
                snaps[coin], spec[coin] = fetch_market(coin, T, first)
            except Exception:
                snaps[coin] = None
        recovery.run_checkpointed(
            T, snaps, caller, cfg, store,
            replay_spec={c: s for c, s in spec.items()
                         if snaps.get(c) is not None},
            crash_at=crash_at)
        sched = mark_completed(store, T)
        publisher_mod.publish_boundary(store, T, len(sched["completed"]),
                                       sched["total"], cfg, publish)
    return sched
