"""OFFICIAL SERVER PREFLIGHT (Mentor Ruling 014.4) — VALIDATION ONLY.

Audited production preflight (hashed into the engine digest). Exactly 18
real Claude requests (3 coins x 3 models x Raw/TA) built from real current
Kraken data through the FROZEN production prompt/config path, plus a direct
HTTPS publication-endpoint probe. Executes NO decisions, opens NO positions,
creates NO trades and NO official schedule, and NEVER reads or writes
data-v1: all writes go to an explicitly supplied SCRATCH store (refused if
it resolves anywhere near data-v1). Secrets are read from the environment
and never printed or persisted.

Hard guards (all required, checked before any network use):
  1. env ARENA_PREFLIGHT_APPROVED=YES-OWNER-MENTOR-AUTHORIZED
  2. env ARENA_APPROVED_MANIFEST_SHA256 / ARENA_APPROVED_SITE_SHA256 —
     externally issued digests; the current tree must match exactly.
  3. env ANTHROPIC_API_KEY present.
  4. scratch store argument outside data-v1.

usage: venv/bin/python scripts/preflight_official.py <scratch-store-dir>
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OFFICIAL_STORE = os.path.join(ROOT, "data-v1")
KRAKEN = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}
APPROVAL_LITERAL = "YES-OWNER-MENTOR-AUTHORIZED"


def refuse(msg):
    print(f"REFUSED: {msg}\nNo model call was made; nothing was written.")
    sys.exit(2)


def check_scratch_store(store):
    """The scratch store must never be, contain, or live inside data-v1."""
    a = os.path.realpath(store)
    b = os.path.realpath(OFFICIAL_STORE)
    if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
        raise ValueError(
            f"scratch store {store!r} overlaps the official data-v1 store")
    return a


def feature_marker_lines(config):
    raw_t = set(config.load_text("prompts/v1/user_raw.txt").splitlines())
    feat_t = config.load_text("prompts/v1/user_feature.txt").splitlines()
    return [ln for ln in feat_t
            if ln.strip() and "{" not in ln and ln not in raw_t]


def run_preflight(store, cfg, snaps, call_fn, T):
    """Core preflight, transport-injected and fully offline-testable.
    call_fn(account_id, system, user) -> (decision|None, response_model,
    stop_reason, latency_s, requested_model) or raises. Returns (summary,
    results); writes only the durable prompt archive + report into `store`.

    Requests run through the PRODUCTION wave structure (rounds.wave_order,
    3 pairs = 6 requests per wave) under the production concurrency limit,
    still making exactly 18 requests and creating no trades or schedule.

    Identity rule (Mentor Ruling 015.4, documented): the response's model id
    must EXACTLY equal the requested model id — no prefix or fuzzy matching.

    PASS requires ALL of: exactly 18 requests/responses, 18 schema-valid,
    18 semantically valid under production decisions.validate(), 18 exact
    model identities, Raw/TA separation, accounts + store unmodified, zero
    transport failures (the direct endpoint probe is verified separately by
    main() into overall_pass)."""
    from concurrent.futures import ThreadPoolExecutor

    from engine import decisions, persistence, prompts, rounds, state
    os.makedirs(store, exist_ok=True)
    accounts = state.init_accounts()             # in-memory only
    assert all(str(a["E"]) == "10000.00" for a in accounts.values())

    seed = f"preflight-{T}"
    entries, rendered = [], {}
    for aid, acct in sorted(accounts.items()):
        system, user = prompts.render(acct, snaps[acct["coin"]], cfg)
        rendered[aid] = (system, user)
        entries.append({"account_id": aid,
                        "pair_id": state.pair_id(acct["coin"], acct["model"]),
                        "round_id": prompts.round_id(acct["coin"], T),
                        "system": system, "user": user,
                        "prompt_hash": hashlib.sha256(
                            user.encode()).hexdigest()})
    persistence.write_prompt_archive(store, seed, entries)
    back = persistence.read_prompt_archive(store, seed)
    archive_ok = all(back[e["account_id"]]["user"] == e["user"]
                     and back[e["account_id"]]["system"] == e["system"]
                     for e in entries)

    from engine import config as config_mod
    markers = feature_marker_lines(config_mod)
    sep_ok = True
    for coin in KRAKEN:
        for model in state.MODELS:
            u_raw = rendered[state.account_id(coin, model, "raw")][1]
            u_ta = rendered[state.account_id(coin, model, "ta")][1]
            if u_raw == u_ta or not any(m in u_ta for m in markers) \
                    or any(m in u_raw for m in markers):
                sep_ok = False

    results = {}
    before = json.dumps(persistence._enc_all(accounts), sort_keys=True,
                        default=str)

    def one(aid):
        system, user = rendered[aid]
        row = {"requested_model": None, "response_model": None,
               "stop_reason": None, "latency_s": None, "accepted": False,
               "schema_valid": False, "semantic_errors": None,
               "identity_ok": False, "transport_error": None}
        try:
            dec, rmodel, stop, latency, want = call_fn(aid, system, user)
            row.update(requested_model=want, response_model=rmodel,
                       stop_reason=stop, latency_s=latency)
            if dec is None:
                row["transport_error"] = f"no tool_use (stop={stop})"
            else:
                row["accepted"] = True
                row["schema_valid"] = decisions.schema_validate(dec) == []
                acct = accounts[aid]
                row["semantic_errors"] = decisions.validate(
                    acct, dec, snaps[acct["coin"]]["P_T"])
                row["decision_position"] = dec.get("position")
                # EXACT identity rule (documented above)
                row["identity_ok"] = rmodel == want
        except Exception as e:
            row["transport_error"] = str(e)[:200]
        return aid, row

    concurrency = cfg["collection"]["concurrency_max_simultaneous_requests"]
    for wave in rounds.wave_order(seed):         # production wave structure
        aids = sorted(state.account_id(coin, model, arm)
                      for coin, model in wave for arm in state.ARMS)
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            for aid, row in ex.map(one, aids):
                results[aid] = row

    after = json.dumps(persistence._enc_all(accounts), sort_keys=True,
                       default=str)
    unmutated = (before == after
                 and not os.path.exists(os.path.join(store, "state.json"))
                 and not os.path.exists(
                     os.path.join(store, "pilot_schedule.json")))
    n = len(results)
    summary = {
        "T_prompt_boundary_utc": T, "n": n,
        "accepted": sum(r["accepted"] for r in results.values()),
        "schema_valid": sum(r["schema_valid"] for r in results.values()),
        "identity_ok": sum(r["identity_ok"] for r in results.values()),
        "transport_failures": [aid for aid, r in results.items()
                               if r["transport_error"]],
        "semantically_valid_first_try": sum(
            1 for r in results.values() if r["semantic_errors"] == []),
        "prompt_archive_durable": archive_ok,
        "raw_ta_separation_ok": sep_ok,
        "accounts_unmutated": unmutated,
        "marks": {c: str(s["P_T"]) for c, s in snaps.items()},
    }
    summary["identity_rule"] = ("response model id must EXACTLY equal the "
                                "requested model id")
    # STRICT PASS (Mentor Ruling 015.4): semantic validity is REQUIRED —
    # 18 accepted, 18 schema-valid, 18 semantically valid, 18 exact
    # identities, zero transport failures, separation + isolation proven.
    summary["model_calls_pass"] = (
        summary["accepted"] == n == 18 and summary["schema_valid"] == 18
        and summary["identity_ok"] == 18
        and summary["semantically_valid_first_try"] == 18
        and not summary["transport_failures"] and archive_ok and sep_ok
        and unmutated)
    out = os.path.join(store, f"preflight_report_{T}.json")
    with open(out, "w") as f:                    # sanitized: no secrets, no
        json.dump({"summary": summary, "results": results}, f, indent=1)
    return summary, results, out


def endpoint_probe(public_dir, fetch, fetch_sha, clock, sleep, timeout=60):
    """Verify the direct HTTPS publication endpoint (Ruling 1) with a probe
    payload, then restore whatever was being served (or remove the probe if
    nothing was). Returns True on verified round-trip."""
    from engine import official
    payload_path = os.path.join(public_dir,
                                official.DirectPublisher.PAYLOAD)
    sha_path = os.path.join(public_dir, official.DirectPublisher.CHECKSUM)
    prior = {p: open(p, "rb").read() for p in (payload_path, sha_path)
             if os.path.exists(p)}
    probe_id = f"preflight-probe:{int(clock())}"
    text = ('window.ARENA_LIVE = {"publication_id": "%s", '
            '"mode": "PREFLIGHT_PROBE"};\n' % probe_id)
    dp = official.DirectPublisher(public_dir, fetch, clock, sleep,
                                  fetch_sha=fetch_sha)
    dp.deadline = clock() + timeout
    try:
        dp(text)
        return True
    finally:                                     # always restore the endpoint
        for p in (payload_path, sha_path):
            if p in prior:
                with open(p, "wb") as f:
                    f.write(prior[p])
            elif os.path.exists(p):
                os.remove(p)


def kraken_ohlc(pair, interval_min):
    """Source-precision preserving (Ruling 019.3): Kraken strings carried
    verbatim into Decimal conversion — never through Python float."""
    url = (f"https://api.kraken.com/0/public/OHLC?pair={pair}"
           f"&interval={interval_min}")
    with urllib.request.urlopen(url, timeout=20) as r:
        raw = json.loads(r.read().decode())
    key = [k for k in raw["result"] if k != "last"][0]
    return [{"t": int(x[0]), "o": x[1], "h": x[2],
             "l": x[3], "c": x[4], "v": x[6]}
            for x in raw["result"][key]]


def main():
    if os.environ.get("ARENA_PREFLIGHT_APPROVED") != APPROVAL_LITERAL:
        refuse("ARENA_PREFLIGHT_APPROVED not set to the owner/mentor "
               "authorization literal")
    eng = os.environ.get("ARENA_APPROVED_MANIFEST_SHA256", "").strip().lower()
    site = os.environ.get("ARENA_APPROVED_SITE_SHA256", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", eng) \
            or not re.fullmatch(r"[0-9a-f]{64}", site):
        refuse("externally issued 64-hex approved digests required "
               "(ARENA_APPROVED_MANIFEST_SHA256 / ARENA_APPROVED_SITE_SHA256)")
    from engine import config, marketdata, official
    config.check_approved_digest(eng)            # Halt A before any network
    config.check_approved_site_digest(site)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        refuse("ANTHROPIC_API_KEY not set")
    if len(sys.argv) < 2:
        refuse("scratch store argument required "
               "(usage: preflight_official.py <scratch-store-dir>)")
    try:
        store = check_scratch_store(sys.argv[1])
    except ValueError as e:
        refuse(str(e))

    cfg = config.load_config()
    schema = config.load_schema()
    T = int(time.time()) // 3600 * 3600
    snaps = {c: marketdata.build_snapshot(c, kraken_ohlc(p, 1),
                                          kraken_ohlc(p, 60),
                                          kraken_ohlc(p, 1440), T)
             for c, p in KRAKEN.items()}
    common = cfg["request_payloads"]["common"]

    def call_fn(aid, system, user):
        model_id = cfg["models"][aid.split("_")[1]]["model"]
        body = {"model": model_id, "max_tokens": common["max_tokens"],
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "tools": [schema], "tool_choice": common["tool_choice"]}
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
        t0 = time.monotonic()
        with urllib.request.urlopen(req,
                                    timeout=common["timeout_seconds"]) as r:
            resp = json.loads(r.read().decode())
        latency = round(time.monotonic() - t0, 2)
        dec = None
        for block in resp.get("content", []):
            if block.get("type") == "tool_use":
                dec = block["input"]
        return dec, resp.get("model"), resp.get("stop_reason"), latency, \
            model_id

    summary, results, report = run_preflight(store, cfg, snaps, call_fn, T)
    for aid, row in results.items():
        print(f"{aid:16s} accepted={row['accepted']} "
              f"schema={row['schema_valid']} identity={row['identity_ok']} "
              f"sem={len(row['semantic_errors'] or [])}"
              f"{' ERR=' + row['transport_error'] if row['transport_error'] else ''}")

    # direct HTTPS endpoint probe against the canonical frozen URL
    public_dir = os.environ.get("ARENA_PUBLIC_DIR", "/var/www/arena")

    def fetch():
        cb = urllib.parse.quote(str(time.time()))
        req = urllib.request.Request(
            f"{official.OFFICIAL_PAYLOAD_URL}?cb={cb}",
            headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()

    def fetch_sha():
        base = official.OFFICIAL_PUBLIC_ORIGIN
        with urllib.request.urlopen(
                f"{base}live_payload.sha256?cb="
                + urllib.parse.quote(str(time.time())), timeout=10) as r:
            return r.read().decode()
    try:
        endpoint_ok = endpoint_probe(public_dir, fetch, fetch_sha,
                                     time.time, time.sleep)
    except Exception as e:
        endpoint_ok = False
        print(f"endpoint probe failed: {str(e)[:200]}")
    summary["direct_endpoint_ok"] = endpoint_ok
    summary["overall_pass"] = bool(summary["model_calls_pass"]
                                   and endpoint_ok)
    # ATTESTATION FIELDS (Mentor Ruling 016.7): the durable report binds the
    # exact tree, endpoint and time this preflight validated. arm_official.py
    # requires the report + its SHA-256 and the runner reverifies both.
    summary["engine_digest"] = eng
    summary["site_digest"] = site
    summary["canonical_endpoint"] = official.OFFICIAL_PAYLOAD_URL
    summary["timestamp"] = int(time.time())
    with open(report, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=1)
    report_sha = hashlib.sha256(open(report, "rb").read()).hexdigest()
    with open(report + ".sha256", "w") as f:
        f.write(f"{report_sha}  {os.path.basename(report)}\n")
    print(json.dumps(summary, indent=1))
    print("report:", report)
    print("report sha256:", report_sha)
    print("OFFICIAL PREFLIGHT:",
          "PASS" if summary["overall_pass"] else "FAIL")
    if summary["overall_pass"]:
        print("To arm: scripts/arm_official.py --confirm ... "
              f"--preflight-report {report} --preflight-sha {report_sha}")
    sys.exit(0 if summary["overall_pass"] else 1)


if __name__ == "__main__":
    main()
