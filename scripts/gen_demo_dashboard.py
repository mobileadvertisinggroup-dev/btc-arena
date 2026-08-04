"""Generate the CLEARLY-LABELED mock/demo dashboard payload for the new site.

Offline only: fixtures + scripted decisions. Writes docs/demo_payload.js.
"""
import json
import os
import sys
import tempfile
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from conftest import T0, ScriptedCaller, flat_decision, long_decision, load_fix  # noqa
from engine import (config, state, marketdata, recovery, persistence,  # noqa
                    dashboard, metrics)

cfg = config.load_config()
snapshots = {c: marketdata.build_snapshot(c, load_fix(c, "1m"), load_fix(c, "1h"),
                                          load_fix(c, "1d"), T0)
             for c in ("BTC", "ETH", "SOL")}

script = {}
for coin in ("BTC", "ETH", "SOL"):
    p = snapshots[coin]["P_T"]
    d_raw = long_decision(p, 6500)
    d_raw["thesis"] = ("[DEMO] Range low held twice on the hourly candles; "
                       "risking 3% against the prior swing low.")
    script[f"{coin.lower()}_haiku_raw"] = d_raw
    d_ta = long_decision(p, 4200)
    d_ta["thesis"] = ("[DEMO] RSI 41.2 with price 1.1% under VWAP; small long "
                      "against the SMA(50).")
    script[f"{coin.lower()}_haiku_ta"] = d_ta
    s_raw = {"position": "short", "size_usd": 3000,
             "stop_loss": float(p * Decimal("1.03")),
             "take_profit": float(p * Decimal("0.94")),
             "thesis": "[DEMO] Lower highs on the daily closes; fading strength.",
             "invalidation": {"timeframe": "1h_close",
                              "operator": "price_at_or_above",
                              "level": float(p * Decimal("1.04"))},
             "watch_condition": None}
    script[f"{coin.lower()}_sonnet_raw"] = s_raw
    f1 = dict(flat_decision(), thesis="[DEMO] Volume ratio 0.86 — waiting.",
              watch_condition={"timeframe": "1h_close",
                               "operator": "price_at_or_above",
                               "level": float(p * Decimal("1.02"))})
    script[f"{coin.lower()}_sonnet_ta"] = f1
    script[f"{coin.lower()}_opus_raw"] = dict(
        flat_decision(), thesis="[DEMO] No edge in this chop; preserving capital.")
    script[f"{coin.lower()}_opus_ta"] = dict(
        flat_decision(), thesis="[DEMO] ATR compressed; flat until expansion.")

accounts = state.init_accounts()
store = tempfile.mkdtemp(prefix="arena-demo-")
persistence.save_state(store + "/state.json", accounts, {"boundary": None})
config.write_launch_manifest(store)
after = {c: [x for x in marketdata.to_dec(load_fix(c, "1m"))
             if T0 <= x["t"] < T0 + 2 * 3600] for c in ("BTC", "ETH", "SOL")}
spec = {c: {"start": T0, "end": T0 + 2 * 3600, "candles": after[c]} for c in after}
ledger, _, _ = recovery.run_checkpointed(T0, snapshots, ScriptedCaller(script),
                                         cfg, store, replay_spec=spec)
accounts, meta = persistence.load_state(store + "/state.json")
manifest = config.build_manifest()
pl = dashboard.payload(accounts, ledger, snapshots,
                       {"ts": T0 + 7200, "code_hash": manifest["combined"]},
                       manifest, cfg)
pl["mode"] = "PREPARATION"
pl["banner"] = "AKRA ARENA — PREPARATION MODE — EXPERIMENT NOT STARTED"
pl["data_notice"] = ("ALL DATA ON THIS PAGE IS MOCK/DEMO DATA generated "
                     "offline from fixtures. No model has been called; no "
                     "experiment has started.")
# demo chart series: two honest points (start -> current demo equity),
# used ONLY inside the labeled demonstration scenario
for coin in ("BTC", "ETH", "SOL"):
    for a in pl["coins"][coin]["accounts"]:
        a["series"] = [{"t": 0, "equity": "10000.00", "fees": "0"},
                       {"t": 1, "equity": a["equity"], "fees": a["fees"]}]
out = os.path.join(ROOT, "docs", "demo_payload.js")
with open(out, "w") as f:
    f.write("window.ARENA_DEMO = ")
    json.dump(pl, f, default=str)
    f.write(";\n")
print("wrote", out, "| pairs:", len([e for e in ledger if e.get("status")]))

# HONEST PRE-START payload: 18 fresh accounts, zero activity, no fabrication
fresh = state.init_accounts()
pre = dashboard.payload(fresh, [], {c: None for c in ("BTC", "ETH", "SOL")},
                        None, manifest, cfg)
pre["mode"] = "PREPARATION"
pre["banner"] = "AKRA ARENA — PREPARATION MODE — EXPERIMENT NOT STARTED"
pre["data_notice"] = ("No model has been called. No paper-trading pilot or "
                      "official experiment has started.")
pre["pilot_progress"] = {"done": 0, "total": None}
for coin in ("BTC", "ETH", "SOL"):
    for a in pre["coins"][coin]["accounts"]:
        a["status_text"] = "Waiting for start"
        a["series"] = [{"t": 0, "equity": "10000.00", "fees": "0"},
                       {"t": 1, "equity": "10000.00", "fees": "0"}]
out2 = os.path.join(ROOT, "docs", "prestart_payload.js")
with open(out2, "w") as f:
    f.write("window.ARENA_PRESTART = ")
    json.dump(pre, f, default=str)
    f.write(";\n")
print("wrote", out2)
