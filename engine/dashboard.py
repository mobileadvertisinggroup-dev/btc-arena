"""Dashboard payload generation (data model only — no HTML, no deployment)."""
from . import state, metrics, config


def payload(accounts, ledger, snapshots, heartbeat, manifest, cfg):
    marks = {c: s["P_T"] for c, s in snapshots.items() if s}
    per_coin = {}
    for coin in state.COINS:
        mark = marks.get(coin)
        coin_accounts = [a for a in accounts.values() if a["coin"] == coin]
        per_coin[coin] = {
            "accounts": [metrics.account_outcomes(a, mark) if mark else
                         metrics.account_outcomes(a, a["E"]) for a in coin_accounts],
            "pairs": [{"model": m,
                       "raw": state.account_id(coin, m, "raw"),
                       "feature": state.account_id(coin, m, "ta")}
                      for m in state.MODELS],
            "rounds": [e for e in ledger if e["round_id"].startswith(f"v1-{coin}-")],
        }
    statuses = [e["status"] for e in ledger]
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
