"""OFFICIAL 14-DAY EXPERIMENT SERVICE — INSTALLED ARMED/OFF, NEVER SELF-ACTIVATING.

Runs under systemd (deploy/arena-official.service, Restart=on-failure —
crashes/reboots recover; clean completion stays down). Mentor
Ruling 6: the service may run indefinitely in ARMED/OFF state — zero model
calls, zero scheduling, data-v1 untouched — until the OWNER's explicit
activation command (scripts/arm_official.py --confirm) writes
control/official_activation.json containing the EXTERNALLY issued
mentor-approved digests and the chosen start hour. Every loop iteration
re-validates the activation record, the digests (Halt A on mismatch), and the
environment before anything else happens.

Publication (Mentor Ruling 1): DirectPublisher writes the payload + checksum
into ARENA_PUBLIC_DIR (served read-only by Nginx) and verifies the exact
publication id + SHA-256 from ARENA_PUBLIC_PAYLOAD_URL — the DIRECT VPS
endpoint, never GitHub Pages. If ARENA_DEPLOY_TOKEN is present, an
asynchronous best-effort GitHub mirror pushes each committed payload AFTER
publication; it can never consume the boundary budget or gate trading.

Environment (systemd EnvironmentFile=/etc/arena/arena.env, never in repo):
  ANTHROPIC_API_KEY          required at activation
  ARENA_PUBLIC_DIR           default /var/www/arena
  ARENA_PUBLIC_PAYLOAD_URL   must equal the canonical frozen endpoint
                             https://live.akraarena.online/live_payload.js
                             (or be unset; any other value refuses)
  ARENA_DEPLOY_TOKEN         optional, mirror only
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OFFICIAL_STORE = os.path.join(ROOT, "data-v1")
CONTROL_DIR = os.path.join(ROOT, "control")
ACTIVATION = os.path.join(CONTROL_DIR, "official_activation.json")
LOCK_PATH = os.path.join(CONTROL_DIR, "runner.lock")
SNAPSHOT_DIR = os.path.join(ROOT, "evidence-official", "daily")
ARMED_POLL_S = 30
KRAKEN = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}
APPROVAL_LITERAL = "YES-OFFICIAL-RUN-APPROVED"


def public_dir():
    return os.environ.get("ARENA_PUBLIC_DIR", "/var/www/arena")


def read_activation():
    """The owner's activation record, or None while ARMED/OFF. Malformed or
    incomplete records are treated as absent (and reported via health)."""
    try:
        with open(ACTIVATION) as f:
            a = json.load(f)
    except Exception:
        return None
    ok = (a.get("approved") == APPROVAL_LITERAL
          and isinstance(a.get("engine_digest"), str)
          and len(a["engine_digest"]) == 64
          and isinstance(a.get("site_digest"), str)
          and len(a["site_digest"]) == 64
          and isinstance(a.get("start_utc"), int)
          and a["start_utc"] % 3600 == 0
          and a.get("total") == 336
          # Mentor Ruling 016.7: activation carries a preflight attestation
          and isinstance(a.get("preflight"), dict)
          and a["preflight"].get("report_path")
          and a["preflight"].get("report_sha256"))
    return a if ok else None


def fetch_market(coin, T, first):
    from engine import marketdata

    def ohlc(pair, interval_min):
        url = (f"https://api.kraken.com/0/public/OHLC?pair={pair}"
               f"&interval={interval_min}")
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = json.loads(r.read().decode())
        key = [k for k in raw["result"] if k != "last"][0]
        return [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
                 "l": float(x[3]), "c": float(x[4]), "v": float(x[6])}
                for x in raw["result"][key]]
    pair = KRAKEN[coin]
    k1m, k1h, k1d = ohlc(pair, 1), ohlc(pair, 60), ohlc(pair, 1440)
    snap = marketdata.build_snapshot(coin, k1m, k1h, k1d, T)
    spec = {"start": T if first else T - 3600, "end": T,
            "candles": marketdata.to_dec(k1m)}
    return snap, spec


def live_caller_factory(cfg):
    """Real Anthropic Messages caller returning the AUDITED RESPONSE ENVELOPE
    (Mentor Ruling 016.4): parsed decision + actual response model id +
    response id + stop reason + measured latency + token usage + the raw API
    response for private audit. engine.rounds archives the envelope verbatim
    and rejects any response whose actual model id differs from the
    requested frozen id (it never executes). The validation retry appends the
    fixed rejection text as a second user message, exactly as the frozen
    config describes."""
    import time as time_mod

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
        thinking = cfg["request_payloads"]["per_model"].get(
            model_id, {}).get("thinking")
        if isinstance(thinking, dict):
            body["thinking"] = thinking
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(), method="POST",
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": common["anthropic-version"],
                     "content-type": "application/json"})
        t0 = time_mod.monotonic()
        try:
            with urllib.request.urlopen(req,
                                        timeout=common["timeout_seconds"]) as r:
                resp = json.loads(r.read().decode())
        except Exception as e:
            raise rounds.TransportError(str(e)[:200])
        latency_ms = round((time_mod.monotonic() - t0) * 1000)
        dec = None
        for block in resp.get("content", []):
            if block.get("type") == "tool_use":
                dec = block["input"]
        if dec is None:
            raise rounds.TransportError(
                f"no tool_use (stop={resp.get('stop_reason')})")
        return {"decision": dec,
                "response_model": resp.get("model"),
                "response_id": resp.get("id"),
                "stop_reason": resp.get("stop_reason"),
                "latency_ms": latency_ms,
                "token_usage": resp.get("usage"),
                # full API response for the private attempts archive; the
                # request key is never part of a response body
                "raw_response": json.dumps(resp, default=str)}
    return caller


def verify_public_binding():
    """Mentor Ruling 014.2: the runner is BOUND to the single canonical
    integrity-locked endpoint (engine.official.OFFICIAL_PAYLOAD_URL, part of
    the audited engine digest). ARENA_PUBLIC_PAYLOAD_URL may only confirm it;
    any other value => refusal BEFORE state initialization, provisioning, or
    model calls."""
    from engine import official
    url = os.environ.get("ARENA_PUBLIC_PAYLOAD_URL",
                         official.OFFICIAL_PAYLOAD_URL)
    if url != official.OFFICIAL_PAYLOAD_URL:
        print("REFUSED: runtime endpoint does not match the integrity-locked "
              f"official endpoint {official.OFFICIAL_PAYLOAD_URL}. Zero "
              "model calls, zero state writes.")
        sys.exit(2)
    return url


def direct_publisher():
    """Ruling 1: write locally, verify from the direct public VPS endpoint —
    always the canonical integrity-locked URL (Ruling 014.2)."""
    from engine import official
    url = official.OFFICIAL_PAYLOAD_URL

    def fetch():
        cb = urllib.parse.quote(str(time.time()))
        req = urllib.request.Request(
            f"{url}?cb={cb}",
            headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()

    def fetch_sha():
        base = url.rsplit("/", 1)[0]
        req = urllib.request.Request(
            f"{base}/{official.DirectPublisher.CHECKSUM}?cb="
            + urllib.parse.quote(str(time.time())),
            headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()
    return official.DirectPublisher(public_dir(), fetch, time.time,
                                    time.sleep, fetch_sha=fetch_sha)


def mirror_factory():
    """Asynchronous historical GitHub mirror (Ruling 1.5) — best-effort only,
    launched by the engine AFTER direct publication, never inside a budget.
    Absent token => no mirror. Failures are recorded in health notes only."""
    token = os.environ.get("ARENA_DEPLOY_TOKEN")
    if not token:
        return None

    def mirror(T):
        src = os.path.join(OFFICIAL_STORE, "publish", f"{T}.js")
        out = os.path.join(ROOT, "docs", "live_payload.js")
        with open(src) as f:
            text = f.read()
        tmp = out + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)
        from dulwich import porcelain
        porcelain.add(ROOT, paths=[out])
        porcelain.commit(ROOT, message=b"official: mirror live payload",
                         author=b"arena-official <official@btc-arena.local>",
                         committer=b"arena-official <official@btc-arena.local>")
        porcelain.push(
            ROOT,
            f"https://x-access-token:{token}@github.com/"
            "mobileadvertisinggroup-dev/btc-arena.git",
            "v1-clean-experiment")
    return mirror


def armed_off_loop():
    """Idle until a valid activation record exists. Health shows ARMED_OFF;
    zero model calls, zero scheduling, zero writes to data-v1."""
    from engine import official
    while True:
        act = read_activation()
        if act is not None:
            return act
        official.write_health(public_dir(), {
            "state": "ARMED_OFF", "pid": os.getpid(), "mode": official.MODE,
            "updated": time.time(),
            "note": "installed; awaiting owner activation "
                    "(scripts/arm_official.py)"})
        time.sleep(ARMED_POLL_S)


def verify_store_integrity():
    """Mentor Ruling 017.1: the CURRENT tree is verified against the store's
    immutable approved manifests BEFORE anything may touch or reconcile
    state. Halt A on mismatch; a store with no manifests yet has nothing to
    protect."""
    from engine import config
    if os.path.exists(os.path.join(OFFICIAL_STORE,
                                   config.LAUNCH_MANIFEST_NAME)):
        config.verify_integrity(config.load_launch_manifest(OFFICIAL_STORE))
        config.verify_site_integrity(config.load_site_manifest(OFFICIAL_STORE))


def main():
    from engine import config, official
    lock = official.acquire_runner_lock(LOCK_PATH)   # noqa: F841 (held open)
    verify_public_binding()          # Ruling 014.2: bound before ANYTHING
    # RESTART STATE MACHINE (Mentor Ruling 017.1):
    #   no_schedule -> fresh activation + strict preflight attestation
    #   unstarted   -> exact bound activation; disarm behavior retained
    #   started     -> resume from persisted schedule + stored attestation;
    #                  the external control file, the 24h freshness test and
    #                  READY publication no longer apply
    #   complete    -> COMPLETE health, clean exit, zero publications/calls
    verify_store_integrity()         # integrity BEFORE any reconciliation
    kind = official.classify_official_store(OFFICIAL_STORE)
    if kind == "complete":
        official.write_health(public_dir(), official.health_info(
            OFFICIAL_STORE, "COMPLETE", time.time()))
        print("OFFICIAL RUN ALREADY COMPLETE: all boundaries terminal. "
              "Zero publications, zero model calls. Exiting cleanly.")
        return
    if kind == "started":
        stored = os.path.join(OFFICIAL_STORE,
                              official.ACCEPTED_ACTIVATION_NAME)
        if os.path.exists(stored):
            print(f"RESUMING started schedule from the durable accepted "
                  f"activation ({stored}).")
        else:
            print("RESUMING started schedule (per contract the activation "
                  "record is no longer consulted after boundary 1).")
        cfg = config.load_config()
        publish = direct_publisher()
        sched = official.run_official(
            OFFICIAL_STORE, cfg, live_caller_factory(cfg), fetch_market,
            publish, time.time, time.sleep, health_dir=public_dir(),
            mirror=mirror_factory(), snapshot_dir=SNAPSHOT_DIR)
        print(f"OFFICIAL RUN COMPLETE: {len(sched['completed'])}/"
              f"{sched['total']} boundaries terminal. "
              "Now run scripts/archive_official.py")
        return
    # DISARM SURVIVES STOP/REBOOT (Ruling 015.2): an UNSTARTED schedule whose
    # bound activation record is absent or changed is rolled back — the
    # service comes up genuinely ARMED/OFF.
    status = official.reconcile_unstarted_schedule(OFFICIAL_STORE, ACTIVATION)
    if status == "rolled_back":
        print("STALE UNSTARTED SCHEDULE ROLLED BACK (activation record "
              "absent or changed while the service was down). ARMED/OFF.")
    while True:
        act = armed_off_loop()
        # INTEGRITY GATES (Rulings 010.1/011.1): the EXTERNALLY issued
        # digests in the owner's activation record must match the current
        # tree BEFORE any state initialization or network use.
        config.check_approved_digest(act["engine_digest"])
        config.check_approved_site_digest(act["site_digest"])
        # ACTIVATION IS BOUND TO A PASSING PREFLIGHT (Ruling 016.7): the
        # service REVERIFIES the attestation — report bytes, PASS, digests,
        # endpoint, freshness — before creating the first schedule.
        try:
            official.verify_preflight_attestation(
                act, act["engine_digest"], act["site_digest"], time.time())
        except official.PreflightAttestationError as e:
            official.write_health(public_dir(), {
                "state": "ARMED_OFF", "pid": os.getpid(),
                "mode": official.MODE, "updated": time.time(),
                "note": f"activation refused: {e}"})
            print(f"REFUSED: {e}. No model call was made; ARMED/OFF.")
            time.sleep(ARMED_POLL_S)
            continue
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("REFUSED: ANTHROPIC_API_KEY not set. No model call was "
                  "made.")
            sys.exit(2)
        act_sha = official.activation_sha(ACTIVATION)
        # re-reconcile with the CURRENT record: a leftover unstarted
        # schedule bound to a different activation rolls back so the newly
        # requested start can provision (Ruling 015.2)
        official.reconcile_unstarted_schedule(OFFICIAL_STORE, ACTIVATION)
        print(f"ACTIVATION ACCEPTED sha256={act_sha} "
              f"start={act['start_utc']} total={act['total']}")
        sched = official.provision_official(
            OFFICIAL_STORE, act["engine_digest"], act["site_digest"],
            act["start_utc"], total=act["total"], activation_sha=act_sha)
        # DURABLE ATTESTATION ARCHIVE (Ruling 017.1): the accepted activation
        # record and verified preflight report are copied into the store so a
        # started run resumes without the external control file or the
        # scratch report.
        official.archive_activation(OFFICIAL_STORE, act, act_sha)
        cfg = config.load_config()
        publish = direct_publisher()
        print(f"OFFICIAL RUN ARMED — {sched['total']} boundaries from "
              f"T0={sched['start']} (UTC) | {official.BANNER}")
        try:
            # REAL PRE-START DISARM (Ruling 014.1): the exact activation
            # record (byte-identical SHA) is revalidated while waiting for
            # the first boundary; deletion/replacement/modification before
            # boundary 1 starts returns the service to ARMED/OFF with zero
            # model calls. After boundary 1 starts, only a service stop
            # halts the run (restart recovery semantics preserved).
            sched = official.run_official(
                OFFICIAL_STORE, cfg, live_caller_factory(cfg), fetch_market,
                publish, time.time, time.sleep, health_dir=public_dir(),
                mirror=mirror_factory(), snapshot_dir=SNAPSHOT_DIR,
                disarm_check=official.make_disarm_check(ACTIVATION, act_sha))
        except official.Disarmed as e:
            # Ruling 016.8: the public payload from the unstarted run is
            # removed too — the public state visibly returns to ARMED_OFF
            official.rollback_unstarted(OFFICIAL_STORE,
                                        public_dir=public_dir())
            official.write_health(public_dir(), {
                "state": "ARMED_OFF", "pid": os.getpid(),
                "mode": official.MODE, "updated": time.time(),
                "note": f"pre-start disarm honored ({e}); zero model calls"})
            print(f"DISARMED before the first boundary ({e}). Zero model "
                  "calls were made; unstarted schedule rolled back; service "
                  "returns to ARMED/OFF.")
            continue
        print(f"OFFICIAL RUN COMPLETE: {len(sched['completed'])}/"
              f"{sched['total']} boundaries terminal. "
              "Now run scripts/archive_official.py")
        return                       # clean exit 0: Restart=on-failure stays down


if __name__ == "__main__":
    main()
