"""Deterministic 3-coin, 18-account mock boundary producing the evidence pack
samples. Offline: fixtures only, scripted decisions, zero network."""
import json
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from conftest import T0, ScriptedCaller, flat_decision, long_decision, load_fix  # noqa
from engine import (config, state, marketdata, rounds, prompts, persistence,  # noqa
                    metrics, dashboard, replay as replay_mod)

EV = os.path.join(ROOT, "evidence")
os.makedirs(EV, exist_ok=True)


def w(name, obj):
    with open(os.path.join(EV, name), "w") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, indent=1, default=str)
    print("wrote", name)


snapshots = {c: marketdata.build_snapshot(c, load_fix(c, "1m"), load_fix(c, "1h"),
                                          load_fix(c, "1d"), T0)
             for c in ("BTC", "ETH", "SOL")}
accounts = state.init_accounts()
cfg = config.load_config()

# terminal-split demo: btc_opus_raw enters the boundary already terminal
accounts["btc_opus_raw"]["terminal"] = True
accounts["btc_opus_raw"]["E"] = Decimal("0")
accounts["btc_opus_raw"]["terminal_info"] = {"t": T0 - 7200, "cause": "liquidation"}

script = {}
for coin in ("BTC", "ETH", "SOL"):
    p = snapshots[coin]["P_T"]
    # haiku pair: raw longs with tight intrabar invalidation (will latch in replay)
    inv = {"timeframe": "1m_intrabar", "operator": "price_at_or_below",
           "level": float(p * Decimal("0.999"))}
    d = long_decision(p, 4000)
    d["invalidation"] = inv
    d["stop_loss"] = None
    script[f"{coin.lower()}_haiku_raw"] = d
    script[f"{coin.lower()}_haiku_ta"] = long_decision(p, 6000)
    # sonnet pair on SOL: raw fails validation twice => PAIR_ABORTED
    if coin == "SOL":
        bad = dict(long_decision(p), size_usd=-1)
        script["sol_sonnet_raw"] = [bad, bad]
    else:
        script[f"{coin.lower()}_sonnet_raw"] = flat_decision()
    script[f"{coin.lower()}_sonnet_ta"] = flat_decision()
    # opus pairs flat (BTC opus = terminal split; ta twin trades alone)
    script[f"{coin.lower()}_opus_raw"] = flat_decision()
    script[f"{coin.lower()}_opus_ta"] = flat_decision()

import tempfile
caller = ScriptedCaller(script)
_store = tempfile.mkdtemp(prefix="arena-mockrun-")
persistence.save_state(_store + "/state.json", accounts, {"boundary": None})
from engine import recovery
ledger, archive, parchive = recovery.run_checkpointed(
    T0, snapshots, caller, cfg, _store)
accounts, _meta = persistence.load_state(_store + "/state.json")

# one common post-T replay per coin (2 h of fixture 1m candles after T)
records = []
for coin in ("BTC", "ETH", "SOL"):
    after = [c for c in marketdata.to_dec(load_fix(coin, "1m"))
             if T0 <= c["t"] < T0 + 2 * 3600]
    rounds.post_boundary_replay(accounts, coin, after, records)

manifest = config.build_manifest()
persistence.save_state(os.path.join(EV, "state_after.json"), accounts,
                       {"boundary": T0, "code_hash": manifest["combined"]})

# samples
w("ledger.json", ledger)
w("replay_records.json", records)
w("attempt_archive_sample.json",
  [a for a in archive if a["account_id"].startswith("sol_sonnet_raw")]
  + [a for a in archive if a["account_id"] == "btc_haiku_raw"])
sys_p, _ = prompts.render(accounts["eth_haiku_raw"], snapshots["ETH"], cfg)
w("prompt_system_ETH.txt", sys_p)
w("prompt_raw_eth_haiku.txt", parchive["eth_haiku_raw"])
w("prompt_feature_eth_haiku.txt", parchive["eth_haiku_ta"])
trig = {aid: accounts[aid]["lifecycle"] for aid in accounts
        if accounts[aid].get("lifecycle") and accounts[aid]["lifecycle"]["triggered"]}
w("invalidation_triggers.json", trig)
w("terminal_split.json", [e for e in ledger if e["status"] == "PAIR_TERMINAL_SPLIT"])
w("pair_records_sample.json",
  {"committed": [e for e in ledger if e["status"] == "PAIR_COMMITTED"][:2],
   "aborted": [e for e in ledger if e["status"] == "PAIR_ABORTED"]})
w("dashboard_payload.json",
  dashboard.payload(accounts, ledger, snapshots,
                    {"ts": T0 + 120, "code_hash": manifest["combined"]}, manifest, cfg))
w("reliability_metrics.json", metrics.reliability(archive, ledger))

print("\nledger summary:")
for e in ledger:
    print(f"  {e['round_id']} {e['pair']:<12} {e['status']}"
          + (f" ({e['reason']})" if e.get("reason") else ""))
print(f"attempts archived: {len(archive)} | prompts archived: {len(parchive)}")
print(f"invalidation latches: {list(trig)}")
