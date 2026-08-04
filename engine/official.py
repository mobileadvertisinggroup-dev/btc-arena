"""OFFICIAL 14-DAY RUN orchestration (Mentor Rulings 1, 2, 3, 6, 8).

Differences from the pilot loop (engine.pilot), each mandated by a ruling:

Ruling 1 — publication must not depend on GitHub Pages. Publication is an
atomic write of live_payload.js (+ its .sha256) into a local directory served
read-only by Nginx on the VPS; verification fetches the payload back from the
DIRECT public HTTPS endpoint and requires the exact publication id AND the
exact SHA-256 of the payload text. GitHub is only an asynchronous mirror
(fire-and-forget thread) that can never consume the deadline or gate trading.

Ruling 2 — explicit sub-budgets inside the unchanged hard T+12:00 deadline:
    T+00:05  market fetch begins (frozen boundary data)
    T+00:30  THINKING payload durable; late/failed coin fetches are
             DATA_UNAVAILABLE (existing semantics)
    T+01:00  direct public THINKING verification deadline — not verified
             => the boundary aborts with ZERO model calls
             (abort_all_reason="thinking_not_verified"; marks still freeze;
             the boundary still becomes terminal — never replayed)
    T+08:30  coordinator collection deadline (model calls + approved retries)
    T+10:30  resolution/accounting complete (by construction: resolution is
             local and immediately follows collection)
    T+11:30  final payload durable + directly published (later => persisted
             FAILED, publication-only retry; trading never re-executes)
    T+12:00  absolute hard stop (coordinator invariant, unchanged)

Ruling 3 — 336 hourly boundaries, canonical UTC scheduling, schedule sealed
at provisioning before any model call, restarts resume the SAME schedule,
missed boundaries abort honestly and are never replayed or backfilled. At the
final boundary open positions are preserved and final equity comes from the
frozen final Kraken marks (the engine has no forced-close path).

Ruling 6 — one-runner-only OS lock, health.json in the public dir, ARMED/OFF
handled by scripts/run_official_14d.py (this module never self-activates).

Ruling 8 — per-boundary evidence is the engine's existing durable trail; this
module adds sealed daily store snapshots (tar.gz + SHA-256 manifest).
"""
import hashlib
import json
import os
import tarfile
import threading

from . import config as config_mod  # noqa: F401  (re-exported for scripts)
from . import persistence, pilot, publisher as publisher_mod, recovery

HOUR = 3600
TOTAL_BOUNDARIES = 336
MODE = "OFFICIAL_14D"
BANNER = ("OFFICIAL 14-DAY EXPERIMENT — REAL AI DECISIONS — PAPER MONEY")
BRANDING = {"mode": MODE, "banner": BANNER}

# THE canonical integrity-locked public origin (Mentor Ruling 014.2). The
# dashboard's LIVE_ORIGIN, the runner's verification endpoint, and the Nginx
# template all derive from or are tested against these exact values; the
# runner refuses BEFORE any state initialization or model call on mismatch.
OFFICIAL_PUBLIC_ORIGIN = "https://live.akraarena.online/"
OFFICIAL_PAYLOAD_URL = OFFICIAL_PUBLIC_ORIGIN + "live_payload.js"

# Ruling 2 sub-budgets, seconds after the scheduled boundary T
FETCH_START_S = 5
THINKING_DURABLE_S = 30
THINKING_VERIFIED_S = 60
COLLECTION_DEADLINE_S = 510          # T+08:30
RESOLUTION_DEADLINE_S = 630          # T+10:30 — ENFORCED (Ruling 014.5):
                                     # passed to the coordinator as both the
                                     # resolution_deadline (late pairs abort
                                     # deadline_exceeded) and replay_deadline
                                     # (late replay => CATCHUP_REQUIRED with
                                     # the watermark preserved)
FINAL_PUBLISH_S = 690                # T+11:30
HARD_DEADLINE_S = 720                # T+12:00 — must equal the frozen config
READY_TIMEOUT_S = 300                # pre-first-boundary READY gate budget
RECONCILE_TIMEOUT_S = 60             # publication-only retry budget/boundary

ABORT_THINKING = "thinking_not_verified"
COINS = ("BTC", "ETH", "SOL")


class LockError(Exception):
    """A second runner instance tried to start (Ruling 6)."""


class PristineError(Exception):
    """The official store's state is not exactly pristine at first-schedule
    creation (Mentor Ruling 015.1): refuse before any schedule, publication,
    network access, or model call."""


class Disarmed(Exception):
    """The owner's activation record was deleted/replaced/modified BEFORE the
    first scheduled boundary (Mentor Ruling 014.1): the run must stop with
    zero model calls and the service must return to ARMED_OFF. After the
    first boundary has started this exception is never raised — halting a
    live run requires an explicit service stop (restart recovery applies)."""


def activation_sha(path):
    """SHA-256 of the exact activation record bytes, or None if unreadable."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def make_disarm_check(path, expected_sha):
    """Returns still_armed(): True only while the EXACT original activation
    record (byte-identical SHA) is still in place. Deleting, replacing, or
    modifying the file makes it False."""
    def still_armed():
        return activation_sha(path) == expected_sha
    return still_armed


def verify_pristine_official_state(store):
    """Mentor Ruling 015.1: before the FIRST official schedule may exist, the
    complete official state must be exactly pristine — byte-normalized equal
    to a freshly initialized 18-account roster (exact ids, $10,000.00 each,
    zero qty/entry/fees/trades/lifecycles/theses/decisions) with a null
    boundary and no history of any kind. A correctly checksummed but DIRTY
    state refuses loudly; a missing state file is fine (standard pristine
    state gets created by provisioning)."""
    import json as _json
    from . import state as state_mod
    spath = os.path.join(store, "state.json")
    if not os.path.exists(spath):
        return True
    accounts, meta = persistence.load_state(spath, expect_full_roster=True)
    expected = state_mod.init_accounts()
    got = _json.dumps(persistence._enc_all(accounts), sort_keys=True,
                      default=str)
    want = _json.dumps(persistence._enc_all(expected), sort_keys=True,
                       default=str)
    if got != want:
        diff = [aid for aid in expected
                if _json.dumps(persistence._enc_all(
                    {aid: accounts.get(aid)}), sort_keys=True, default=str)
                != _json.dumps(persistence._enc_all(
                    {aid: expected[aid]}), sort_keys=True, default=str)]
        raise PristineError(
            f"official state is not pristine (accounts differ: {diff[:6]})")
    if meta.get("boundary") is not None:
        raise PristineError("official meta carries a boundary")
    for key in ("equity_history", "equity_history_last", "marks", "marks_T",
                "finalized_pairs", "outbox", "flushed_ids",
                "replay_watermark", "replay_state", "coin_terminated",
                "boundary_complete", "_recovering"):
        if meta.get(key):
            raise PristineError(f"official meta carries prior-run {key}")
    return True


BINDING_NAME = "activation_binding.json"


def write_activation_binding(store, activation_sha, engine_digest,
                             site_digest, start, total):
    """Mentor Ruling 015.2: an UNSTARTED schedule is bound to the exact
    activation record (its SHA-256) plus the digests, start, and total it
    was provisioned from. The binding travels with the store so a service
    stop or reboot cannot orphan a stale schedule."""
    path = os.path.join(store, BINDING_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"activation_sha": activation_sha,
                   "engine_digest": engine_digest,
                   "site_digest": site_digest,
                   "start": start, "total": total}, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def reconcile_unstarted_schedule(store, activation_path):
    """Mentor Ruling 015.2 startup reconciliation. Returns one of:
      'no_schedule'  — nothing provisioned; nothing to do.
      'started'      — a boundary has started or completed: the schedule is
                       IMMUTABLE; normal restart recovery applies.
      'match'        — unstarted schedule and the current activation record
                       is byte-identical (SHA) to the bound one with the
                       same digests/start/total: resume waiting.
      'rolled_back'  — unstarted schedule whose activation is absent,
                       changed, or unbound: schedule/publication artifacts
                       safely removed; the service is back to ARMED/OFF and
                       a later re-arm provisions the newly requested
                       schedule."""
    try:
        sched = pilot.load_schedule(store)
    except pilot.ScheduleError:
        return "no_schedule"
    _, meta = persistence.load_state(os.path.join(store, "state.json"),
                                     expect_full_roster=True)
    if sched["completed"] or meta.get("boundary") is not None:
        return "started"                 # replacement forbidden forever
    binding = None
    try:
        with open(os.path.join(store, BINDING_NAME)) as f:
            binding = json.load(f)
    except Exception:
        binding = None
    cur_sha = activation_sha(activation_path)
    ok = (binding is not None and cur_sha is not None
          and binding.get("activation_sha") == cur_sha
          and binding.get("start") == sched["start"]
          and binding.get("total") == sched["total"])
    if ok:
        try:
            with open(activation_path) as f:
                act = json.load(f)
        except Exception:
            act = {}
        ok = (act.get("engine_digest") == binding.get("engine_digest")
              and act.get("site_digest") == binding.get("site_digest")
              and act.get("start_utc") == binding.get("start")
              and act.get("total") == binding.get("total"))
    if ok:
        return "match"
    rollback_unstarted(store)
    return "rolled_back"


def rollback_unstarted(store):
    """Undo provisioning artifacts after a valid PRE-START disarm: allowed
    ONLY while zero boundaries are completed and account state carries no
    boundary. Removes the sealed schedule, the publication log, and durable
    payloads so a later re-arm provisions a fresh schedule. Any sign the run
    started => refuse loudly (halting a live run is a service-stop concern)."""
    import shutil
    try:
        sched = pilot.load_schedule(store)
        if sched["completed"]:
            raise RuntimeError("boundaries already completed")
    except pilot.ScheduleError:
        return False                    # nothing provisioned; nothing to do
    _, meta = persistence.load_state(os.path.join(store, "state.json"),
                                     expect_full_roster=True)
    if meta.get("boundary") is not None:
        raise RuntimeError("boundary state exists — not an unstarted run")
    for name in (pilot.SCHEDULE_NAME, publisher_mod.PUB_LOG, BINDING_NAME):
        try:
            os.remove(os.path.join(store, name))
        except FileNotFoundError:
            pass
    shutil.rmtree(os.path.join(store, "publish"), ignore_errors=True)
    return True


def acquire_runner_lock(path):
    """One-runner-only protection: exclusive non-blocking OS lock. The
    returned file object must be kept referenced for the runner's lifetime;
    a concurrent holder => LockError, refuse to run."""
    import fcntl
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    f = open(path, "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise LockError(f"another runner holds {path}")
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def provision_official(store, approved_digest, approved_site_digest, start,
                       total=TOTAL_BOUNDARIES, activation_sha=None):
    """Digest-gated provisioning of the official schedule (Ruling 3): the
    start must be an exact UTC hour; the full boundary list is persisted
    (sealed) before any model call. Idempotent: an existing schedule/state is
    preserved verbatim — a restart can never mint new boundaries.

    Mentor Ruling 015.1: when this call would create the FIRST schedule, an
    existing state file must be exactly pristine (PristineError otherwise —
    refused before schedule/publication/network/model work; a missing state
    file is created pristine by provisioning).
    Mentor Ruling 015.2: a newly created schedule is bound to the exact
    activation record SHA + digests + start + total via the store binding."""
    if start % HOUR:
        raise pilot.ScheduleError(
            f"official start {start} is not an exact UTC hour")
    creating = not os.path.exists(os.path.join(store, pilot.SCHEDULE_NAME))
    if creating:
        verify_pristine_official_state(store)   # raises BEFORE any write
    sched = pilot.provision(store, approved_digest, approved_site_digest,
                            start, total=total)
    if creating:
        write_activation_binding(store, activation_sha, approved_digest,
                                 approved_site_digest, sched["start"],
                                 sched["total"])
    return sched


class DirectPublisher:
    """Ruling 1 publisher: atomic local write into the Nginx public dir, then
    verification against the DIRECT public endpoint — the fetched payload
    must carry the exact publication id and hash to the exact SHA-256 of the
    published text, strictly BEFORE the per-call absolute deadline the runner
    sets from the Ruling 2 budgets. `fetch()` returns the payload text as the
    public endpoint currently serves it; `fetch_sha()` (optional) returns the
    served .sha256 companion for an extra cross-check. No deadline set =>
    refuse (budget discipline is mandatory)."""

    PAYLOAD = "live_payload.js"
    CHECKSUM = "live_payload.sha256"

    def __init__(self, public_dir, fetch, clock, sleep, interval=2,
                 fetch_sha=None):
        self.public_dir = public_dir
        self.fetch = fetch
        self.fetch_sha = fetch_sha
        self.clock = clock
        self.sleep = sleep
        self.interval = interval
        self.deadline = None            # absolute, in clock's domain

    def _write_atomic(self, name, text):
        os.makedirs(self.public_dir, exist_ok=True)
        path = os.path.join(self.public_dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def __call__(self, text):
        if self.deadline is None:
            raise publisher_mod.PublicationError(
                "DirectPublisher used without an absolute deadline")
        expected_id = publisher_mod.parse_payload_text(text).get(
            "publication_id")
        if not expected_id:
            raise publisher_mod.PublicationError("payload missing publication_id")
        local_sha = hashlib.sha256(text.encode()).hexdigest()
        self._write_atomic(self.PAYLOAD, text)
        self._write_atomic(self.CHECKSUM, local_sha + "\n")
        last = "no fetch attempted"
        while True:
            try:
                remote = self.fetch()
                remote_sha = hashlib.sha256(remote.encode()).hexdigest()
                pl = publisher_mod.parse_payload_text(remote)
                if pl.get("publication_id") == expected_id \
                        and remote_sha == local_sha:
                    if self.fetch_sha is not None:
                        served = self.fetch_sha().strip()
                        if served != local_sha:
                            raise publisher_mod.PublicationError(
                                f"served checksum mismatch: {served[:72]}")
                    # success counts ONLY strictly before the deadline
                    if self.clock() < self.deadline:
                        return remote
                    raise publisher_mod.PublicationError(
                        "direct verification succeeded after the budget "
                        "deadline — treated as FAILED")
                last = (f"id={pl.get('publication_id')!r} "
                        f"sha_match={remote_sha == local_sha}")
            except publisher_mod.PublicationError:
                raise
            except Exception as e:
                last = str(e)[:150]
            if self.clock() >= self.deadline:
                raise publisher_mod.PublicationError(
                    f"direct verification deadline: {last}")
            self.sleep(self.interval)


def _set_deadline(publish, deadline):
    if hasattr(publish, "deadline"):
        publish.deadline = deadline


def write_health(public_dir, info):
    """Atomic read-only health status (Ruling 6) next to the live payload."""
    if public_dir is None:
        return
    os.makedirs(public_dir, exist_ok=True)
    path = os.path.join(public_dir, "health.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(info, f, indent=1, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def health_info(store, state_str, clock, latest_scheduled=None, note=None):
    try:
        sched = pilot.load_schedule(store)
        done, total = sorted(sched["completed"]), sched["total"]
    except Exception:
        done, total = [], None
    log = publisher_mod.read_log(store)
    latest_pub = None
    if log:
        key = sorted(log, key=lambda k: (log[k].get("boundary") or 0, k))[-1]
        e = log[key]
        latest_pub = {"key": key, "publication_id": e.get("publication_id"),
                      "status": e.get("status")}
    return {"state": state_str, "pid": os.getpid(), "updated": clock(),
            "mode": MODE, "boundaries_done": len(done),
            "total": total, "latest_scheduled": latest_scheduled,
            "latest_terminal": (done[-1] if done else None),
            "latest_publication": latest_pub,
            **({"note": note} if note else {})}


def snapshot_store(store, out_dir, label):
    """Sealed daily evidence snapshot (Ruling 8): tar.gz of the store +
    SHA-256 manifest of every file + the tarball's own SHA-256. Existing
    snapshots are never overwritten."""
    os.makedirs(out_dir, exist_ok=True)
    tar_path = os.path.join(out_dir, f"{label}.tar.gz")
    man_path = os.path.join(out_dir, f"{label}.MANIFEST.sha256.json")
    if os.path.exists(tar_path):
        return tar_path, open(tar_path + ".sha256").read().split()[0]
    files = []
    for root, dirs, names in os.walk(store):
        dirs.sort()
        for n in sorted(names):
            if n.endswith(".tmp"):
                continue
            p = os.path.join(root, n)
            files.append((os.path.relpath(p, store), p))
    manifest = {rel: hashlib.sha256(open(p, "rb").read()).hexdigest()
                for rel, p in files}
    with open(man_path, "w") as f:
        json.dump({"files": manifest, "count": len(manifest)}, f, indent=1)
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(man_path, arcname=os.path.basename(man_path))
        for rel, p in files:
            tar.add(p, arcname=os.path.join("store", rel))
    sha = hashlib.sha256(open(tar_path, "rb").read()).hexdigest()
    with open(tar_path + ".sha256", "w") as f:
        f.write(f"{sha}  {os.path.basename(tar_path)}\n")
    return tar_path, sha


def _fire_mirror(mirror, T, notes):
    """Asynchronous historical mirror (Ruling 1.5): never joined inside any
    boundary budget, never raises into the trading loop."""
    def run():
        try:
            mirror(T)
        except Exception as e:
            notes.append(f"mirror {T}: {str(e)[:120]}")
    t = threading.Thread(target=run, name=f"mirror-{T}", daemon=True)
    t.start()
    return t


def run_official(store, cfg, caller, fetch_market, publish, clock, sleep,
                 health_dir=None, mirror=None, crash_at=None,
                 snapshot_dir=None, disarm_check=None):
    """Drive the sealed official schedule exactly once per boundary under the
    Ruling 2 budgets. Restart-safe exactly like the pilot loop: an incomplete
    persisted boundary is recovered first (non-finalized pairs abort as
    crash_recovery), and the SAME schedule is always resumed.

    `disarm_check` (Mentor Ruling 014.1): still_armed() predicate polled ONLY
    until the first boundary's work begins. If the owner's exact activation
    record disappears or changes before then, Disarmed is raised with zero
    model calls and zero boundary work. Once boundary 1 has started the
    predicate is never consulted again — halting requires a service stop."""
    assert cfg["collection"]["collection_deadline_seconds"] == HARD_DEADLINE_S
    sched = pilot.load_schedule(store)
    notes = []

    def health(state_str, T=None):
        try:
            write_health(health_dir,
                         health_info(store, state_str, clock,
                                     latest_scheduled=T,
                                     note="; ".join(notes[-3:]) or None))
        except Exception:
            pass                        # health can never affect trading

    def _pre_start_disarmed():
        return (disarm_check is not None and not sched["completed"]
                and not disarm_check())

    # CLEAN COMPLETION (Mentor Ruling 014.3): a finished experiment stays
    # finished — no READY republication, no publications, no model calls.
    if all(T in sched["completed"] for T in sched["boundaries"]):
        health("COMPLETE")
        return sched

    if _pre_start_disarmed():
        raise Disarmed("activation record gone/changed before READY")
    health("STARTING")
    _set_deadline(publish, clock() + READY_TIMEOUT_S)
    publisher_mod.reconcile(store, cfg, publish, branding=BRANDING)
    publisher_mod.publish_ready(store, cfg, publish, branding=BRANDING)
    spath = os.path.join(store, "state.json")
    for T in sched["boundaries"]:
        if T in sched["completed"]:
            continue
        _, meta = persistence.load_state(spath, expect_full_roster=True)
        if meta.get("boundary") is not None \
                and not meta.get("boundary_complete"):
            recovery.recover(store)     # frozen rule: same boundary first
        # publication-only retry of any earlier FAILED committed payload;
        # runs outside this boundary's budgets (we are before T)
        if clock() < T - RECONCILE_TIMEOUT_S:
            _set_deadline(publish, clock() + RECONCILE_TIMEOUT_S)
            publisher_mod.reconcile(store, cfg, publish, branding=BRANDING)
        health("WAITING", T)
        while clock() < T + FETCH_START_S:
            if _pre_start_disarmed():
                raise Disarmed("activation record gone/changed before the "
                               "first scheduled boundary")
            sleep(max(1, min(30, T + FETCH_START_S - clock())))
        # last pre-start check, immediately before boundary work begins;
        # from here on only an explicit service stop halts the run
        if _pre_start_disarmed():
            raise Disarmed("activation record gone/changed at the first "
                           "boundary gate")
        health("BOUNDARY_ACTIVE", T)
        # ---- freeze boundary data: budget ends at T+00:30 ----
        snaps, spec = {}, {}
        first = not sched["completed"]
        for coin in COINS:
            if clock() >= T + THINKING_DURABLE_S:
                snaps[coin] = None      # honest: budget exhausted
                continue
            try:
                snaps[coin], spec[coin] = fetch_market(coin, T, first)
            except Exception:
                snaps[coin] = None
        # ---- THINKING durable + DIRECT public verification by T+01:00 ----
        done, total = len(sched["completed"]), sched["total"]
        _set_deadline(publish, T + THINKING_VERIFIED_S)
        entry = publisher_mod.publish_thinking(store, T, done, total, cfg,
                                               publish, branding=BRANDING)
        abort_reason = (None if entry["status"] == "PUBLISHED"
                        else ABORT_THINKING)
        # ---- model calls under the T+08:30 collection deadline ----
        recovery.run_checkpointed(
            T, snaps, caller, cfg, store,
            replay_spec={c: s for c, s in spec.items()
                         if snaps.get(c) is not None},
            crash_at=crash_at, clock=clock,
            deadline=T + COLLECTION_DEADLINE_S,
            abort_all_reason=abort_reason,
            resolution_deadline=T + RESOLUTION_DEADLINE_S,
            replay_deadline=T + RESOLUTION_DEADLINE_S)
        sched = pilot.mark_completed(store, T)
        # ---- terminal payload durable + direct publication by T+11:30 ----
        _set_deadline(publish, T + FINAL_PUBLISH_S)
        publisher_mod.publish_boundary(store, T, len(sched["completed"]),
                                       sched["total"], cfg, publish,
                                       branding=BRANDING)
        if mirror is not None:
            _fire_mirror(mirror, T, notes)
        if snapshot_dir is not None and len(sched["completed"]) % 24 == 0:
            try:                        # evidence duty, never gates trading
                day = (T - sched["start"]) // (24 * HOUR) + 1
                snapshot_store(store, snapshot_dir, f"day{day:02d}-{T}")
            except Exception as e:
                notes.append(f"snapshot {T}: {str(e)[:120]}")
        health("WAITING", T)
    health("COMPLETE")
    return sched
