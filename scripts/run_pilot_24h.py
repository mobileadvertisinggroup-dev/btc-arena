"""24-HOUR VISIBLE PAPER-TRADING PILOT — PREPARED, NOT ACTIVATED.

NOT AUDITED FOR LAUNCH. Hard-guarded: refuses to run unless BOTH
  1. env  ARENA_PILOT_APPROVED=YES-AUDIT-PASSED   (set only after ChatGPT's
     independent source audit approves activation), and
  2. the literal flag  --activate  is passed.

When (later) activated it will: fetch real current Kraken OHLC, ask the real
Claude models (Haiku/Sonnet/Opus, Raw vs Feature) for hourly decisions on
temporary $10,000 paper accounts for 24 boundaries, drive the audited
coordinator (engine.recovery), and publish the dashboard payload labeled:

  24-HOUR PILOT — REAL AI DECISIONS — PAPER MONEY — NOT OFFICIAL EXPERIMENTAL EVIDENCE

"Real AI decisions" = the Claude models actually decide; no real money.
Pilot accounts are TEMPORARY: they are archived and never reused (see
scripts/archive_pilot_reset.py).
"""
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PILOT_BANNER = ("24-HOUR PILOT — REAL AI DECISIONS — PAPER MONEY — "
                "NOT OFFICIAL EXPERIMENTAL EVIDENCE")
PILOT_STORE = os.path.join(ROOT, "data-pilot-24h")
KRAKEN = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}


def guard():
    if os.environ.get("ARENA_PILOT_APPROVED") != "YES-AUDIT-PASSED" \
            or "--activate" not in sys.argv:
        print("REFUSED: pilot not activated.\n"
              "Requires env ARENA_PILOT_APPROVED=YES-AUDIT-PASSED and the "
              "--activate flag,\ngranted only after independent audit "
              "approval. No model call was made.")
        sys.exit(2)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("REFUSED: ANTHROPIC_API_KEY not set.")
        sys.exit(2)


def kraken_ohlc(pair, interval_min):
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval_min}"
    with urllib.request.urlopen(url, timeout=20) as r:
        raw = json.loads(r.read().decode())
    key = [k for k in raw["result"] if k != "last"][0]
    return [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
             "l": float(x[3]), "c": float(x[4]), "v": float(x[6])}
            for x in raw["result"][key]]


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


def main():
    guard()
    from engine import config, state, marketdata, recovery, persistence, dashboard
    cfg = config.load_config()
    manifest = config.build_manifest()
    os.makedirs(PILOT_STORE, exist_ok=True)
    spath = os.path.join(PILOT_STORE, "state.json")
    if not os.path.exists(spath):
        persistence.save_state(spath, state.init_accounts(), {"boundary": None})
    caller = live_caller_factory(cfg)
    start = (int(time.time()) // 3600 + 1) * 3600
    print(f"PILOT ACTIVE — 24 boundaries from T0={start}")
    for i in range(24):
        T = start + i * 3600
        while time.time() < T + 120:                    # off-minute grace
            time.sleep(30)
        snaps, spec = {}, {}
        for coin, pair in KRAKEN.items():
            try:
                k1m = kraken_ohlc(pair, 1)
                k1h = kraken_ohlc(pair, 60)
                k1d = kraken_ohlc(pair, 1440)
                snaps[coin] = marketdata.build_snapshot(coin, k1m, k1h, k1d, T)
                spec[coin] = {"start": T - 3600 if i else T, "end": T,
                              "candles": marketdata.to_dec(k1m)}
            except Exception as e:
                print(f"{coin}: DATA_UNAVAILABLE ({e})")
                snaps[coin] = None
        ledger, _, _ = recovery.run_checkpointed(T, snaps, caller, cfg,
                                                 PILOT_STORE, replay_spec=spec)
        accounts, meta = persistence.load_state(spath)
        pl = dashboard.payload(accounts, ledger, snaps,
                               {"ts": int(time.time()),
                                "code_hash": manifest["combined"]},
                               manifest, cfg)
        pl["mode"] = "PILOT_24H"
        pl["banner"] = PILOT_BANNER
        pl["data_notice"] = PILOT_BANNER
        with open(os.path.join(ROOT, "docs", "demo_payload.js"), "w") as f:
            f.write("window.ARENA_DATA = ")
            json.dump(pl, f, default=str)
            f.write(";\n")
        print(f"boundary {i + 1}/24 done: "
              f"{[e.get('status') for e in ledger if e.get('status')]}")
    print("PILOT COMPLETE. Now run scripts/archive_pilot_reset.py --confirm")


if __name__ == "__main__":
    main()
