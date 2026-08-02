"""Deterministic offline mock season (Ruling 005.14): 72 hourly boundaries,
3 coins, 18 accounts, commits/aborts/retries/exits/invalidations/terminal split.
Run twice from fresh state; all output hashes must match.

    python3 scripts/mock_season.py
"""
import hashlib
import json
import os
import shutil
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from conftest import T0, ScriptedCaller, flat_decision, long_decision, load_fix  # noqa
from engine import (config, state, marketdata, recovery, persistence, metrics,  # noqa
                    dashboard, rounds)

HOUR = 3600
N_BOUNDARIES = 72
cfg = config.load_config()
K = {c: {tf: load_fix(c, tf) for tf in ("1m", "1h", "1d")} for c in ("BTC", "ETH", "SOL")}


def decide(aid, T, p, acct_side, idx):
    h = int(hashlib.sha256(f"{aid}:{T}".encode()).hexdigest(), 16)
    if aid == "sol_haiku_raw" and idx == 33:      # ensure flat before terminal setup
        return flat_decision()
    if aid == "sol_haiku_raw" and idx == 35:      # abort pair at the gap hour so the
        bad = dict(long_decision(p), size_usd=-1)  # 5x position rides into replay
        return [bad, bad]
    if aid == "btc_sonnet_raw" and idx % 7 == 3:  # double-invalid => PAIR_ABORTED
        bad = dict(long_decision(p), size_usd=-1)
        return [bad, bad]
    if aid == "eth_opus_raw" and idx % 5 == 2:    # invalid then corrected => retry path
        return [dict(long_decision(p), size_usd=-1), long_decision(p, 1500)]
    r = h % 10
    if acct_side == "flat":
        if r < 4:
            d = long_decision(p, 1000 + (h % 2000))
            d["stop_loss"] = float(Decimal(str(p)) * Decimal("0.97"))
            return d
        return flat_decision()
    if r < 3:
        return flat_decision()                    # close
    hold = {"position": acct_side, "size_usd": None, "stop_loss": None,
            "take_profit": None, "thesis": "hold", "invalidation": None,
            "watch_condition": None}
    return hold


class SeasonCaller:
    def __init__(self, accounts, T, idx, snapshots):
        import threading
        self.accounts, self.T, self.idx, self.snaps = accounts, T, idx, snapshots
        self.scripts = {}
        self._lock = threading.Lock()

    def __call__(self, aid, system, user, retry):
        with self._lock:
            return self._call(aid, system, user, retry)

    def _call(self, aid, system, user, retry):
        a = self.accounts[aid]
        p = self.snaps[a["coin"]]["P_T"]
        if aid == "sol_haiku_raw" and self.idx == 34 and state.side(a) == "flat" \
                and aid not in self.scripts:
            eq = state.equity_at(a, p)
            d = long_decision(p, float(eq * Decimal("4.9")))
            d["stop_loss"] = None
            d["take_profit"] = None
            d["invalidation"] = {"timeframe": "1m_intrabar",
                                 "operator": "price_at_or_below",
                                 "level": float(p * Decimal("0.5"))}
            self.scripts[aid] = [d]
        if aid not in self.scripts:
            item = decide(aid, self.T, float(p), state.side(a), self.idx)
            self.scripts[aid] = item if isinstance(item, list) else [item]
        seq = self.scripts[aid]
        d = seq.pop(0) if seq else flat_decision()
        if d.get("position") in ("long", "short") and d.get("size_usd") is None:
            d = dict(d, size_usd=float(abs(a["qty"]) * p))   # hold = exact current
            if d["size_usd"] < 10:
                d = flat_decision()
        return d


def run_season(store):
    os.makedirs(store, exist_ok=True)
    persistence.save_state(store + "/state.json", state.init_accounts(),
                           {"boundary": None})
    all_ledger = []
    last_snaps = None
    for idx in range(N_BOUNDARIES):
        T = T0 + idx * HOUR
        snaps = {}
        for coin in ("BTC", "ETH", "SOL"):
            try:
                snaps[coin] = marketdata.build_snapshot(
                    coin, K[coin]["1m"], K[coin]["1h"], K[coin]["1d"], T)
            except marketdata.DataUnavailable:
                snaps[coin] = None
        last_snaps = snaps
        accounts, _ = persistence.load_state(store + "/state.json")
        caller = SeasonCaller(accounts, T, idx, snaps)
        after = {c: [x for x in marketdata.to_dec(K[c]["1m"])
                     if T <= x["t"] < T + HOUR] for c in ("BTC", "ETH", "SOL")}
        spec = {c: {"start": T, "end": T + HOUR, "candles": after[c]}
                for c in after}
        led, _, _ = recovery.run_checkpointed(T, snaps, caller, cfg, store,
                                              replay_spec=spec)
        all_ledger.extend(led)
    accounts, meta = persistence.load_state(store + "/state.json")
    attempts = []
    adir = os.path.join(store, "attempts")
    for root, _, fs in os.walk(adir):
        for fn in sorted(fs):
            if fn.endswith(".json"):
                attempts.append(json.load(open(os.path.join(root, fn))))
    rel = metrics.reliability(attempts, all_ledger)
    pl = dashboard.payload(accounts, all_ledger, last_snaps,
                           {"ts": 0, "code_hash": "fixed"},
                           config.build_manifest(), cfg)
    def H(obj):
        return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                              .encode()).hexdigest()
    return {
        "state": H(persistence._enc_all(accounts)),
        "ledger": H(all_ledger),
        "prompts": H(sorted({a["generated_prompt_hash"] for a in attempts})),
        "metrics": H(rel),
        "dashboard": H(pl),
        "counts": {s: sum(1 for e in all_ledger if e.get("status") == s)
                   for s in ("PAIR_COMMITTED", "PAIR_ABORTED", "PAIR_TERMINAL_SPLIT")},
        "terminal": [aid for aid, a in accounts.items() if a["terminal"]],
        "n_attempts": len(attempts),
    }


def main():
    base = os.path.join(ROOT, "evidence", "season")
    results = []
    for run_i in (1, 2):
        store = os.path.join(base, f"run{run_i}")
        shutil.rmtree(store, ignore_errors=True)
        results.append(run_season(store))
    r1, r2 = results
    identical = all(r1[k] == r2[k] for k in ("state", "ledger", "prompts",
                                             "metrics", "dashboard"))
    out = {"run1": r1, "run2": r2, "identical": identical}
    with open(os.path.join(ROOT, "evidence", "season_hashes.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out, indent=1))
    assert identical, "SEASON NOT DETERMINISTIC"
    assert r1["counts"]["PAIR_ABORTED"] > 0 and r1["counts"]["PAIR_TERMINAL_SPLIT"] > 0
    assert r1["terminal"], "no terminal account"


if __name__ == "__main__":
    main()
