"""Public dashboard publication (Ruling 010.3).

Publication is strictly decoupled from trading:
  * it runs only AFTER a boundary's atomic engine commit;
  * its failure is persisted as a clear FAILED status and can NEVER cause a
    trading round to execute twice (boundary dedupe lives in the engine
    ledger/checkpoint, which publication never touches);
  * retry (reconcile) re-publishes ONLY — it reads the durable payload files
    and publication log, never engine state, and triggers zero model calls.

The transport is injected: production supplies a git-push publisher
(scripts/run_pilot_12h.py); tests supply fakes. A publisher receives the exact
payload text and returns the text as actually published, which is verified to
contain the expected boundary/progress identifier before PUBLISHED is recorded.
"""
import hashlib
import json
import os

from . import config as config_mod, dashboard, persistence

PUB_LOG = "publications.json"
BANNER = ("12-HOUR PILOT — REAL AI DECISIONS — PAPER MONEY — "
          "NOT OFFICIAL EXPERIMENTAL EVIDENCE")


class PublicationError(Exception):
    """Publication/verification failure. Never escapes into the trading loop:
    it is caught, persisted as status FAILED, and retried publication-only."""


def _log_path(store):
    return os.path.join(store, PUB_LOG)


def read_log(store):
    try:
        with open(_log_path(store)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _write_log(store, log):
    tmp = _log_path(store) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(log, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _log_path(store))


def build_live_payload(store, T, done, total, cfg):
    """Build the live payload purely from PERSISTED post-commit engine state
    (never from in-memory values), stamped with the boundary + progress
    identifiers the verifier requires."""
    spath = os.path.join(store, "state.json")
    accounts, _ = persistence.load_state(spath, expect_full_roster=True)
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    manifest = config_mod.load_launch_manifest(store)
    pl = dashboard.payload(accounts, ledger, {c: None for c in ("BTC", "ETH", "SOL")},
                           {"ts": T, "code_hash": manifest["combined"]},
                           manifest, cfg)
    pl["mode"] = "PILOT_12H"
    pl["banner"] = BANNER
    pl["data_notice"] = BANNER
    pl["published_boundary"] = T
    pl["pilot_progress"] = {"done": done, "total": total}
    return pl


def _text(pl):
    return "window.ARENA_LIVE = " + json.dumps(pl, default=str) + ";\n"


def _verify_published(published_text, T, done, total):
    """The published payload must carry the expected round/progress id."""
    try:
        body = published_text.strip()
        body = body[body.index("=") + 1:].rstrip("; \n")
        pl = json.loads(body)
    except Exception as e:
        raise PublicationError(f"published payload unreadable: {e}")
    if pl.get("published_boundary") != T or \
            pl.get("pilot_progress") != {"done": done, "total": total}:
        raise PublicationError(
            "published payload missing expected boundary/progress identifier")


def _attempt(store, log, key, T, done, total, text, publish):
    entry = dict(log.get(key, {}), boundary=T, done=done, total=total,
                 payload_sha256=hashlib.sha256(text.encode()).hexdigest())
    try:
        out = publish(text)
        _verify_published(out if isinstance(out, str) else text, T, done, total)
        entry["status"] = "PUBLISHED"
        entry.pop("reason", None)
    except Exception as e:
        entry["status"] = "FAILED"
        entry["reason"] = str(e)[:300]
    log[key] = entry
    _write_log(store, log)
    return entry


def publish_boundary(store, T, done, total, cfg, publish):
    """Publish exactly once per committed boundary. The payload text is made
    durable BEFORE the transport attempt so a crash or failure can be retried
    from disk without touching the engine. Never raises into the trading loop;
    the outcome is the persisted status."""
    log = read_log(store)
    key = str(T)
    if log.get(key, {}).get("status") == "PUBLISHED":
        return log[key]                       # exactly-once per boundary
    text = _text(build_live_payload(store, T, done, total, cfg))
    pdir = os.path.join(store, "publish")
    os.makedirs(pdir, exist_ok=True)
    ppath = os.path.join(pdir, f"{T}.js")
    tmp = ppath + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ppath)
    return _attempt(store, log, key, T, done, total, text, publish)


def reconcile(store, cfg, publish):
    """Startup/retry path: PUBLICATION ONLY. For completed boundaries whose
    publication is missing or FAILED: the latest one is (re)published from its
    durable payload file (rebuilt from persisted state if the file is absent);
    older ones are marked SUPERSEDED — the public site always shows the latest
    payload, and every non-published boundary keeps an explicit stale status.
    Engine state, the ledger, and the model caller are never touched."""
    from . import pilot as pilot_mod
    try:
        sched = pilot_mod.load_schedule(store)
    except Exception:
        return []
    log = read_log(store)
    pending = [T for T in sched["completed"]
               if log.get(str(T), {}).get("status") != "PUBLISHED"]
    if not pending:
        return []
    latest = max(sched["completed"])
    results = []
    for T in sorted(pending):
        key = str(T)
        if T != latest:
            entry = dict(log.get(key, {}), boundary=T, status="SUPERSEDED")
            log[key] = entry
            _write_log(store, log)
            results.append((T, "SUPERSEDED"))
            continue
        done, total = len(sched["completed"]), sched["total"]
        ppath = os.path.join(store, "publish", f"{T}.js")
        if os.path.exists(ppath):
            text = open(ppath).read()
        else:
            text = _text(build_live_payload(store, T, done, total, cfg))
        entry = _attempt(store, log, key, T, done, total, text, publish)
        results.append((T, entry["status"]))
    return results
