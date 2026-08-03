"""PHASE 1 PREFLIGHT (Mentor Final Ruling) — VALIDATION ONLY.

18 real Claude requests (3 coins x 3 models x Raw/Feature) built from real
current Kraken data through the FROZEN production prompt/config path.

Executes NO decisions, opens NO positions, charges NO fees, mutates NO pilot
account, advances NO pilot progress. Accounts are fresh in-memory $10,000
objects; the only writes are the durable preflight prompt archive and a JSON
report, both OUTSIDE any pilot store.

This tool lives in tools/ (not hashed): the approved engine digest is
verified at startup and cannot change. Requests are built to be byte-
equivalent to scripts/run_pilot_12h.live_caller_factory (same cfg payloads),
with the response's model identity additionally captured for verification.
"""
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config, state, prompts, decisions, marketdata, persistence  # noqa: E402

APPROVED_ENGINE = "c425200e1b840524bb444288d6725bc1f728f60d94d72ed725d0fc704ec5432c"
APPROVED_SITE = "2bfff2cb28b970c09a76b5a9045c4ffee909d37ccd8f39ab69001a95c264b7fa"
KRAKEN = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}


def kraken_ohlc(pair, interval_min):
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval_min}"
    with urllib.request.urlopen(url, timeout=20) as r:
        raw = json.loads(r.read().decode())
    key = [k for k in raw["result"] if k != "last"][0]
    return [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4]), "v": float(x[6])}
            for x in raw["result"][key]]


def call_model(cfg, schema, account_id, system, user):
    """Byte-equivalent to the production caller's request; also returns the
    response's model identity and stop reason for verification."""
    common = cfg["request_payloads"]["common"]
    model_key = account_id.split("_")[1]
    model_id = cfg["models"][model_key]["model"]
    body = {"model": model_id, "max_tokens": common["max_tokens"],
            "system": system, "messages": [{"role": "user", "content": user}],
            "tools": [schema], "tool_choice": common["tool_choice"]}
    thinking = cfg["request_payloads"]["per_model"].get(model_id, {}).get("thinking")
    if isinstance(thinking, dict):
        body["thinking"] = thinking
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(), method="POST",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": common["anthropic-version"],
                 "content-type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=common["timeout_seconds"]) as r:
        resp = json.loads(r.read().decode())
    latency = round(time.monotonic() - t0, 2)
    dec = None
    for block in resp.get("content", []):
        if block.get("type") == "tool_use":
            dec = block["input"]
    return dec, resp.get("model"), resp.get("stop_reason"), latency, model_id


def feature_marker_lines():
    raw_t = set(config.load_text("prompts/v1/user_raw.txt").splitlines())
    feat_t = config.load_text("prompts/v1/user_feature.txt").splitlines()
    return [ln for ln in feat_t
            if ln.strip() and "{" not in ln and ln not in raw_t]


def main():
    # frozen-tree gate: identical to the mentor-approved digests or refuse
    assert config.build_manifest()["combined"] == APPROVED_ENGINE, \
        "engine digest mismatch — preflight refused"
    assert config.build_site_manifest()["combined"] == APPROVED_SITE, \
        "site digest mismatch — preflight refused"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("REFUSED: ANTHROPIC_API_KEY not set")
        sys.exit(2)
    cfg = config.load_config()
    schema = config.load_schema()
    T = int(time.time()) // 3600 * 3600          # last completed hour boundary
    store = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(ROOT, "..", "preflight-store")
    os.makedirs(store, exist_ok=True)

    snaps = {}
    for coin, pair in KRAKEN.items():
        snaps[coin] = marketdata.build_snapshot(
            coin, kraken_ohlc(pair, 1), kraken_ohlc(pair, 60),
            kraken_ohlc(pair, 1440), T)
    accounts = state.init_accounts()             # in-memory only, $10,000 each
    assert all(str(a["E"]) == "10000.00" for a in accounts.values())

    # render all 18 prompts via the frozen production path + durable archive
    seed = f"preflight-{T}"
    entries, rendered = [], {}
    for aid, acct in sorted(accounts.items()):
        system, user = prompts.render(acct, snaps[acct["coin"]], cfg)
        rendered[aid] = (system, user)
        entries.append({"account_id": aid,
                        "pair_id": state.pair_id(acct["coin"], acct["model"]),
                        "round_id": prompts.round_id(acct["coin"], T),
                        "system": system, "user": user,
                        "prompt_hash": __import__("hashlib").sha256(
                            user.encode()).hexdigest()})
    persistence.write_prompt_archive(store, seed, entries)
    back = persistence.read_prompt_archive(store, seed)
    archive_ok = all(back[e["account_id"]]["user"] == e["user"] and
                     back[e["account_id"]]["system"] == e["system"]
                     for e in entries)

    # Raw vs Feature separation on the RENDERED prompts
    markers = feature_marker_lines()
    sep_ok = True
    for coin in KRAKEN:
        for model in ("haiku", "sonnet", "opus"):
            u_raw = rendered[state.account_id(coin, model, "raw")][1]
            u_ta = rendered[state.account_id(coin, model, "ta")][1]
            if u_raw == u_ta or not any(m in u_ta for m in markers) \
                    or any(m in u_raw for m in markers):
                sep_ok = False

    results, before = {}, json.dumps(
        persistence._enc_all(accounts), sort_keys=True, default=str)
    for aid in sorted(accounts):
        system, user = rendered[aid]
        row = {"requested_model": None, "response_model": None,
               "stop_reason": None, "latency_s": None, "accepted": False,
               "schema_valid": False, "semantic_errors": None,
               "identity_ok": False, "transport_error": None}
        try:
            dec, rmodel, stop, latency, want = call_model(
                cfg, schema, aid, system, user)
            row.update(requested_model=want, response_model=rmodel,
                       stop_reason=stop, latency_s=latency)
            if dec is None:
                row["transport_error"] = f"no tool_use (stop={stop})"
            else:
                row["accepted"] = True
                try:
                    decisions.schema_validate(dec)
                    row["schema_valid"] = True
                except Exception as e:
                    row["schema_error"] = str(e)[:200]
                acct = accounts[aid]
                row["semantic_errors"] = decisions.validate(
                    acct, dec, snaps[acct["coin"]]["P_T"])
                row["decision_position"] = dec.get("position")
                row["identity_ok"] = bool(rmodel) and (
                    rmodel.startswith(want) or want.startswith(rmodel))
        except Exception as e:
            row["transport_error"] = str(e)[:200]
        results[aid] = row
        print(f"{aid:16s} accepted={row['accepted']} schema={row['schema_valid']}"
              f" identity={row['identity_ok']} pos={row.get('decision_position')}"
              f" sem={len(row['semantic_errors'] or [])} {row['latency_s']}s"
              f"{' ERR=' + row['transport_error'] if row['transport_error'] else ''}")

    # validation-only proof: accounts byte-identical, no store state written
    after = json.dumps(persistence._enc_all(accounts), sort_keys=True,
                       default=str)
    unmutated = (before == after) and not os.path.exists(
        os.path.join(store, "state.json"))

    n = len(results)
    summary = {
        "T_prompt_boundary_utc": T,
        "accepted": sum(r["accepted"] for r in results.values()),
        "schema_valid": sum(r["schema_valid"] for r in results.values()),
        "identity_ok": sum(r["identity_ok"] for r in results.values()),
        "transport_failures": [aid for aid, r in results.items()
                               if r["transport_error"]],
        "semantically_valid_first_try": sum(
            1 for r in results.values() if r["semantic_errors"] == []),
        "prompt_archive_durable": archive_ok,
        "raw_feature_separation_ok": sep_ok,
        "accounts_unmutated": unmutated,
        "n": n,
        "marks": {c: str(s["P_T"]) for c, s in snaps.items()},
    }
    summary["model_calls_pass"] = (
        summary["accepted"] == n == 18 and summary["schema_valid"] == 18
        and summary["identity_ok"] == 18 and not summary["transport_failures"]
        and archive_ok and sep_ok and unmutated)
    out = os.path.join(store, f"preflight_report_{T}.json")
    json.dump({"summary": summary, "results": results}, open(out, "w"),
              indent=1)
    print(json.dumps(summary, indent=1))
    print("report:", out)
    print("MODEL-CALL PREFLIGHT:",
          "PASS" if summary["model_calls_pass"] else "FAIL")
    sys.exit(0 if summary["model_calls_pass"] else 1)


if __name__ == "__main__":
    main()
