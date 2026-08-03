"""Public dashboard publication (Rulings 010.3 + 011).

Publication is strictly decoupled from trading:
  * it runs only AFTER the relevant persisted engine state exists;
  * its failure is persisted as a clear FAILED status and can NEVER cause a
    trading round to execute twice (boundary dedupe lives in the engine
    ledger/checkpoint, which publication never touches);
  * retry (reconcile) re-publishes ONLY — never engine state, never a model.

Per boundary TWO lifecycle states are published (Ruling 011.3):
  THINKING        — before any model request: round lifecycle marker only,
                    progress unchanged, no fabricated decision/trade/PnL
                    (payload = previous persisted state + status). This means
                    "requests are processing"; it never exposes chain-of-thought.
  ROUND_COMMITTED — after the atomic engine commit: final theses, decisions,
                    positions, fees, equity, and advanced progress.
Plus a READY state at startup, published and verified BEFORE the first model
call — if it cannot be published, activation refuses with zero model calls.

Payload marks (Ruling 011.2) come ONLY from the engine's persisted boundary
marks; account equity is never a substitute price. The payload also carries
durable per-account equity history: $10,000 start + one point per completed
boundary at that boundary's persisted mark.

The transport is injected. A publisher receives the exact payload text and
returns the text as ACTUALLY PUBLISHED (production: fetched back from the
public URL); it is verified to contain the expected boundary, progress,
lifecycle, and publication id before PUBLISHED is recorded. The static site
is integrity-checked against the store's approved site manifest before every
publication attempt.
"""
import hashlib
import json
import os

from . import config as config_mod, dashboard, persistence

PUB_LOG = "publications.json"
BANNER = ("12-HOUR PILOT — REAL AI DECISIONS — PAPER MONEY — "
          "NOT OFFICIAL EXPERIMENTAL EVIDENCE")
THINKING_NOTICE = ("Model requests are processing for this boundary. This "
                   "status never exposes hidden reasoning; final submitted "
                   "decisions appear after the round commits.")


class PublicationError(Exception):
    """Publication/verification failure. Never escapes into the trading loop
    (recorded as status FAILED), except from publish_ready, which is the
    explicit pre-activation gate."""


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


def _schedule(store):
    from . import pilot as pilot_mod
    return pilot_mod.load_schedule(store)


def build_live_payload(store, T, done, total, cfg, lifecycle):
    """Build the live payload purely from PERSISTED post-commit engine state:
    persisted boundary marks (never live/snapshot data, never cash-as-price),
    persisted equity history, persisted ledger."""
    spath = os.path.join(store, "state.json")
    accounts, meta = persistence.load_state(spath, expect_full_roster=True)
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    manifest = config_mod.load_launch_manifest(store)
    raw_marks = meta.get("marks") or {}
    marks = {c: raw_marks.get(c) for c in ("BTC", "ETH", "SOL")}
    pl = dashboard.payload(accounts, ledger,
                           {c: None for c in ("BTC", "ETH", "SOL")},
                           {"ts": T, "code_hash": manifest["combined"]},
                           manifest, cfg, marks=marks)
    marks_T = meta.get("marks_T")
    pl["marks"] = {c: {"price": marks.get(c), "T": marks_T,
                       "stale": marks.get(c) is not None and marks_T != T,
                       "unavailable": marks.get(c) is None}
                   for c in ("BTC", "ETH", "SOL")}
    # durable equity chart: $10,000 start + one point per completed boundary
    hist = meta.get("equity_history", {})
    try:
        start_t = _schedule(store)["start"] - 3600
    except Exception:
        start_t = (min((p[0]["T"] for p in hist.values() if p), default=T)
                   - 3600)
    for coin in ("BTC", "ETH", "SOL"):
        for row in pl["coins"][coin]["accounts"]:
            row["series"] = (
                [{"t": start_t, "equity": "10000.00", "fees": "0"}] +
                [{"t": p["T"], "equity": p["equity"], "fees": p["fees"]}
                 for p in hist.get(row["id"], [])])
    pl["mode"] = "PILOT_12H"
    pl["banner"] = BANNER
    pl["data_notice"] = BANNER
    pl["round_lifecycle"] = lifecycle
    if lifecycle == "THINKING":
        pl["lifecycle_notice"] = THINKING_NOTICE
    pl["published_boundary"] = T
    pl["pilot_progress"] = {"done": done, "total": total}
    pl["publication_id"] = f"{T}:{lifecycle}:{done}"
    return pl


def _text(pl):
    return "window.ARENA_LIVE = " + json.dumps(pl, default=str) + ";\n"


def parse_payload_text(text):
    body = text.strip()
    return json.loads(body[body.index("=") + 1:].rstrip("; \n"))


def _verify_published(published_text, expected):
    """The publicly published payload must carry the expected boundary,
    progress, lifecycle, and publication id."""
    try:
        pl = parse_payload_text(published_text)
    except Exception as e:
        raise PublicationError(f"published payload unreadable: {e}")
    ok = (pl.get("published_boundary") == expected["T"]
          and pl.get("pilot_progress") == {"done": expected["done"],
                                           "total": expected["total"]}
          and pl.get("round_lifecycle") == expected["lifecycle"]
          and pl.get("publication_id") == expected["publication_id"])
    if not ok:
        raise PublicationError(
            "published payload missing expected boundary/progress/lifecycle "
            "identifier")


def _attempt(store, log, key, expected, text, publish):
    entry = dict(log.get(key, {}),
                 boundary=expected["T"], done=expected["done"],
                 total=expected["total"], lifecycle=expected["lifecycle"],
                 publication_id=expected["publication_id"],
                 payload_sha256=hashlib.sha256(text.encode()).hexdigest())
    try:
        # static-site integrity gate: never publish through a drifted UI
        config_mod.verify_site_integrity(config_mod.load_site_manifest(store))
        out = publish(text)
        _verify_published(out if isinstance(out, str) else text, expected)
        entry["status"] = "PUBLISHED"
        entry.pop("reason", None)
    except Exception as e:
        entry["status"] = "FAILED"
        entry["reason"] = str(e)[:300]
    log[key] = entry
    _write_log(store, log)
    return entry


def _expected(T, done, total, lifecycle):
    return {"T": T, "done": done, "total": total, "lifecycle": lifecycle,
            "publication_id": f"{T}:{lifecycle}:{done}"}


def publish_ready(store, cfg, publish):
    """Pre-activation gate (Ruling 011.3): publish and verify a READY state
    BEFORE the first model call. Raises PublicationError unless the public
    dashboard verifiably received it — the caller must then refuse to trade."""
    sched = _schedule(store)
    done, total = len(sched["completed"]), sched["total"]
    T = sched["start"]
    expected = _expected(T, done, total, "READY")
    text = _text(build_live_payload(store, T, done, total, cfg, "READY"))
    entry = _attempt(store, read_log(store), "ready", expected, text, publish)
    if entry["status"] != "PUBLISHED":
        raise PublicationError(
            f"READY state could not be publicly published "
            f"({entry.get('reason')}); refusing to begin model calls")
    return entry


def publish_thinking(store, T, done, total, cfg, publish):
    """Lifecycle state A — before any model request for boundary T. Progress
    unchanged; payload is previous persisted state + status only. Failure is
    recorded and never blocks or re-executes trading."""
    log = read_log(store)
    key = f"{T}:thinking"
    if log.get(key, {}).get("status") == "PUBLISHED":
        return log[key]
    expected = _expected(T, done, total, "THINKING")
    text = _text(build_live_payload(store, T, done, total, cfg, "THINKING"))
    return _attempt(store, log, key, expected, text, publish)


def publish_boundary(store, T, done, total, cfg, publish):
    """Lifecycle state B — after the atomic engine commit: exactly once per
    committed boundary. The payload text is made durable BEFORE the transport
    attempt so a crash or failure can be retried from disk without touching
    the engine. Never raises into the trading loop."""
    log = read_log(store)
    key = f"{T}:committed"
    if log.get(key, {}).get("status") == "PUBLISHED":
        return log[key]                       # exactly-once per boundary
    expected = _expected(T, done, total, "ROUND_COMMITTED")
    text = _text(build_live_payload(store, T, done, total, cfg,
                                    "ROUND_COMMITTED"))
    pdir = os.path.join(store, "publish")
    os.makedirs(pdir, exist_ok=True)
    ppath = os.path.join(pdir, f"{T}.js")
    tmp = ppath + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ppath)
    return _attempt(store, log, key, expected, text, publish)


def reconcile(store, cfg, publish):
    """Startup/retry path: PUBLICATION ONLY. For completed boundaries whose
    committed publication is missing or FAILED: the latest one is
    (re)published from its durable payload file (rebuilt from persisted state
    if absent); older ones are marked SUPERSEDED — the public site always
    shows the latest payload, and every non-published boundary keeps an
    explicit stale status. Engine state, the ledger, and the model caller are
    never touched."""
    try:
        sched = _schedule(store)
    except Exception:
        return []
    log = read_log(store)
    pending = [T for T in sched["completed"]
               if log.get(f"{T}:committed", {}).get("status") != "PUBLISHED"]
    if not pending:
        return []
    latest = max(sched["completed"])
    results = []
    for T in sorted(pending):
        key = f"{T}:committed"
        if T != latest:
            entry = dict(log.get(key, {}), boundary=T, status="SUPERSEDED")
            log[key] = entry
            _write_log(store, log)
            results.append((T, "SUPERSEDED"))
            continue
        done, total = len(sched["completed"]), sched["total"]
        expected = _expected(T, done, total, "ROUND_COMMITTED")
        ppath = os.path.join(store, "publish", f"{T}.js")
        if os.path.exists(ppath):
            text = open(ppath).read()
        else:
            text = _text(build_live_payload(store, T, done, total, cfg,
                                            "ROUND_COMMITTED"))
        entry = _attempt(store, log, key, expected, text, publish)
        results.append((T, entry["status"]))
    return results
