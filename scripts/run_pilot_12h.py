"""12-HOUR VISIBLE PAPER-TRADING PILOT — PREPARED, NOT ACTIVATED.

NOT AUDITED FOR LAUNCH. Hard-guarded: refuses to run unless ALL of
  1. env  ARENA_PILOT_APPROVED=YES-AUDIT-PASSED  (set only after ChatGPT's
     independent source audit approves activation),
  2. env  ARENA_APPROVED_MANIFEST_SHA256=<64-hex mentor-approved combined
     digest> — issued EXTERNALLY by the independent auditor. The current
     tree's combined manifest must hash to exactly this value BEFORE any
     state initialization, prompt rendering, network access, or model call
     (engine.config.check_approved_digest); otherwise Integrity Halt A with
     zero model calls. The tree can never approve itself (Ruling 010.1).
  3. the literal flag  --activate,
  4. env  ANTHROPIC_API_KEY.

When activated: 12 hourly boundaries on a PERSISTED schedule, real Kraken
OHLC, real Claude models (Haiku/Sonnet/Opus, Raw vs Feature) on temporary
$10,000 paper accounts, the audited coordinator (engine.recovery) driven by
engine.pilot with wired crash recovery (a restart resumes the SAME schedule —
never 12 new boundaries), and post-commit publication of the live dashboard
payload (engine.publisher) labeled:

  12-HOUR PILOT — REAL AI DECISIONS — PAPER MONEY — NOT OFFICIAL EXPERIMENTAL EVIDENCE

"Real AI decisions" = the Claude models actually decide; no real money.
Publication: docs/live_payload.js is written atomically and pushed to the
public GitHub Pages site (env ARENA_DEPLOY_TOKEN). Publication failure never
re-executes a trading round; it is persisted as FAILED and retried
(publication only) at next startup. Pilot accounts are TEMPORARY: archived
and never reused (see scripts/archive_pilot_reset.py).
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PILOT_STORE = os.path.join(ROOT, "data-pilot-12h")
KRAKEN = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}


def guard():
    if os.environ.get("ARENA_PILOT_APPROVED") != "YES-AUDIT-PASSED" \
            or "--activate" not in sys.argv:
        print("REFUSED: pilot not activated.\n"
              "Requires env ARENA_PILOT_APPROVED=YES-AUDIT-PASSED and the "
              "--activate flag,\ngranted only after independent audit "
              "approval. No model call was made.")
        sys.exit(2)
    digest = os.environ.get("ARENA_APPROVED_MANIFEST_SHA256", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        print("REFUSED: ARENA_APPROVED_MANIFEST_SHA256 not set to the "
              "mentor-approved 64-hex combined-manifest digest. The tree "
              "cannot approve itself. No model call was made.")
        sys.exit(2)
    site_digest = os.environ.get("ARENA_APPROVED_SITE_SHA256", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", site_digest):
        print("REFUSED: ARENA_APPROVED_SITE_SHA256 not set to the "
              "mentor-approved 64-hex site-manifest digest. The static UI "
              "cannot approve itself. No model call was made.")
        sys.exit(2)
    # publishing-mechanism preflight (Ruling 011.4): the public publisher and
    # its runtime dependency must be available BEFORE accounts are created or
    # any model is called — publishing-unavailable => refuse here.
    if not os.environ.get("ARENA_DEPLOY_TOKEN"):
        print("REFUSED: ARENA_DEPLOY_TOKEN not set — public publishing is "
              "unavailable, so the visible pilot must not start. No model "
              "call was made.")
        sys.exit(2)
    try:
        import dulwich.porcelain  # noqa: F401  (publisher runtime dependency)
    except Exception as e:
        print(f"REFUSED: publisher dependency unavailable (dulwich: {e}). "
              "No model call was made.")
        sys.exit(2)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("REFUSED: ANTHROPIC_API_KEY not set.")
        sys.exit(2)
    return digest, site_digest


def kraken_ohlc(pair, interval_min):
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval_min}"
    with urllib.request.urlopen(url, timeout=20) as r:
        raw = json.loads(r.read().decode())
    key = [k for k in raw["result"] if k != "last"][0]
    return [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4]), "v": float(x[6])}
            for x in raw["result"][key]]


def fetch_market(coin, T, first):
    """Kraken canonical data -> (snapshot, replay_spec) for one boundary."""
    from engine import marketdata
    pair = KRAKEN[coin]
    k1m = kraken_ohlc(pair, 1)
    k1h = kraken_ohlc(pair, 60)
    k1d = kraken_ohlc(pair, 1440)
    snap = marketdata.build_snapshot(coin, k1m, k1h, k1d, T)
    spec = {"start": T if first else T - 3600, "end": T,
            "candles": marketdata.to_dec(k1m)}
    return snap, spec


def live_caller_factory(cfg):
    """Real Anthropic Messages caller matching the frozen request payloads."""
    from engine import config as config_mod, rounds
    schema = config_mod.load_schema()
    common = cfg["request_payloads"]["common"]

    def caller(account_id, system, user, retry_msg):
        model_key = account_id.split("_")[1]
        model_id = cfg["models"][model_key]["model"]
        messages = [{"role": "user", "content": user}]
        if retry_msg:
            messages.append({"role": "user", "content": retry_msg})
        body = {"model": model_id, "max_tokens": common["max_tokens"],
                "system": system, "messages": messages,
                "tools": [schema],
                "tool_choice": common["tool_choice"]}
        thinking = cfg["request_payloads"]["per_model"].get(model_id, {}).get("thinking")
        if isinstance(thinking, dict):
            body["thinking"] = thinking
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(), method="POST",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": common["anthropic-version"],
                     "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=common["timeout_seconds"]) as r:
                resp = json.loads(r.read().decode())
        except Exception as e:
            raise rounds.TransportError(str(e)[:200])
        for block in resp.get("content", []):
            if block.get("type") == "tool_use":
                return block["input"]
        raise rounds.TransportError(f"no tool_use (stop={resp.get('stop_reason')})")
    return caller


PAGES_URL = ("https://mobileadvertisinggroup-dev.github.io/btc-arena/"
             "live_payload.js")
PUBLIC_VERIFY_TIMEOUT_S = 420          # GitHub Pages deploys take minutes
PUBLIC_VERIFY_INTERVAL_S = 15


def poll_public(expected_id, fetch, clock, sleep,
                timeout=PUBLIC_VERIFY_TIMEOUT_S,
                interval=PUBLIC_VERIFY_INTERVAL_S):
    """Poll the PUBLIC payload URL until it serves the expected publication
    id; return the remote text. A local write alone is never sufficient —
    only the publicly served payload counts. Timeout or persistently wrong
    remote content => PublicationError => recorded FAILED (Ruling 011.4)."""
    from engine import publisher as publisher_mod
    deadline = clock() + timeout
    last = "no fetch attempted"
    while True:
        try:
            remote = fetch()
            pl = publisher_mod.parse_payload_text(remote)
            if pl.get("publication_id") == expected_id:
                return remote
            last = f"public payload has stale id {pl.get('publication_id')!r}"
        except Exception as e:
            last = str(e)[:150]
        if clock() >= deadline:
            raise publisher_mod.PublicationError(
                f"public verification timeout: {last}")
        sleep(interval)


def git_publish(text):
    """Production publisher: atomically write docs/live_payload.js, commit,
    push to the public GitHub Pages branch, then poll the PUBLIC URL with a
    cache-busting query until it serves this exact publication id, and return
    the REMOTE text (engine.publisher re-verifies boundary/progress/lifecycle
    before recording PUBLISHED). Any failure raises PublicationError =>
    persisted FAILED status; committed rounds are never affected."""
    from engine import publisher as publisher_mod
    expected_id = publisher_mod.parse_payload_text(text).get("publication_id")
    if not expected_id:
        raise publisher_mod.PublicationError("payload missing publication_id")
    out = os.path.join(ROOT, "docs", "live_payload.js")
    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)
    token = os.environ.get("ARENA_DEPLOY_TOKEN")
    if not token:
        raise publisher_mod.PublicationError(
            "ARENA_DEPLOY_TOKEN not set: payload written locally but the "
            "public site was NOT updated")
    try:
        from dulwich import porcelain
        porcelain.add(ROOT, paths=[out])
        porcelain.commit(ROOT, message=b"pilot: live dashboard payload update",
                         author=b"arena-pilot <pilot@btc-arena.local>",
                         committer=b"arena-pilot <pilot@btc-arena.local>")
        porcelain.push(
            ROOT,
            f"https://x-access-token:{token}@github.com/"
            "mobileadvertisinggroup-dev/btc-arena.git",
            "v1-clean-experiment")
    except Exception as e:
        raise publisher_mod.PublicationError(f"pages push failed: {e}"[:300])

    def fetch():
        # cache-busting query keyed to the publication id + no-cache headers
        req = urllib.request.Request(
            f"{PAGES_URL}?cb={urllib.parse.quote(expected_id)}",
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode()
    return poll_public(expected_id, fetch, time.monotonic, time.sleep)


def main():
    digest, site_digest = guard()
    from engine import config, pilot, publisher
    # INTEGRITY GATES FIRST (Rulings 010.1/011.1): externally approved engine
    # AND site digests must match the current tree BEFORE any state
    # initialization or network use. provision() is idempotent: a restart
    # keeps the SAME persisted schedule.
    start = (int(time.time()) // 3600 + 1) * 3600
    sched = pilot.provision(PILOT_STORE, digest, site_digest, start)
    cfg = config.load_config()
    print(f"PILOT ACTIVE — {sched['total']} boundaries from "
          f"T0={sched['start']} | {publisher.BANNER}")
    sched = pilot.run_pilot(PILOT_STORE, cfg, live_caller_factory(cfg),
                            fetch_market, git_publish, time.time, time.sleep)
    print(f"PILOT COMPLETE: {len(sched['completed'])}/{sched['total']} "
          "boundaries. Now run scripts/archive_pilot_reset.py --confirm")


if __name__ == "__main__":
    main()
