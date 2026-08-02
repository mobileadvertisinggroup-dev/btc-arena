"""Dashboard payload generation (data model only — no HTML, no deployment)."""
from decimal import Decimal

from . import state, metrics


def _account_detail(a, mark):
    lc = a.get("lifecycle")
    inv = None
    if lc is not None:
        inv = {"timeframe": lc["invalidation"]["timeframe"],
               "operator": lc["invalidation"]["operator"],
               "level": str(lc["invalidation"]["level"]),
               "status": ("TRIGGERED" if lc["triggered"] else "NOT TRIGGERED"),
               "triggered_t": (lc["triggered"] or {}).get("t")}
    upnl = (a["qty"] * (mark - a["entry"])) if a["qty"] and a["entry"] else Decimal(0)
    return {
        "side": state.side(a),
        "qty": str(abs(a["qty"])),
        "notional": str(abs(a["qty"]) * mark) if a["qty"] else "0",
        "entry": str(a["entry"]) if a["entry"] is not None else None,
        "stop": str(a["stop"]) if a["stop"] is not None else None,
        "tp": str(a["tp"]) if a["tp"] is not None else None,
        "unrealized_pnl": str(upnl),
        "thesis": (a["theses"][-1]["text"] if a["theses"] else None),
        "invalidation": inv,
        "watch": ({"level": str(a["watch"]["level"]),
                   "timeframe": a["watch"]["timeframe"],
                   "operator": a["watch"]["operator"],
                   "triggered": bool(a["watch"].get("triggered"))}
                  if a.get("watch") else None),
        "trades": a["trades"][-5:],
    }


def payload(accounts, ledger, snapshots, heartbeat, manifest, cfg):
    marks = {c: s["P_T"] for c, s in snapshots.items() if s}
    per_coin = {}
    for coin in state.COINS:
        mark = marks.get(coin)
        coin_accounts = [a for a in accounts.values() if a["coin"] == coin]
        per_coin[coin] = {
            "accounts": [dict(metrics.account_outcomes(a, mark if mark else a["E"]),
                              **_account_detail(a, mark if mark else a["E"]))
                         for a in coin_accounts],
            "pairs": [{"model": m,
                       "raw": state.account_id(coin, m, "raw"),
                       "feature": state.account_id(coin, m, "ta")}
                      for m in state.MODELS],
            "rounds": [e for e in ledger if e["round_id"].startswith(f"v1-{coin}-")],
        }
    statuses = [e["status"] for e in ledger if e.get("status")]
    return {
        "experiment": cfg["experiment"],
        "label": "V1 EXPERIMENT",
        "question": cfg["question"],
        "hashes": manifest["files"],
        "combined_hash": manifest["combined"],
        "pilot_link": {"href": "pilot/index.html",
                       "label": "PILOT / SYSTEM TEST / NOT VALID EXPERIMENTAL EVIDENCE"},
        "health": {"heartbeat": heartbeat,
                   "stale": heartbeat is None},
        "round_counts": {s: statuses.count(s) for s in
                         ("PAIR_COMMITTED", "PAIR_ABORTED", "PAIR_TERMINAL_SPLIT")},
        "coins": per_coin,
    }
