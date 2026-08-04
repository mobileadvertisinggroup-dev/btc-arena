"""Dashboard payload generation (data model only — no HTML, no deployment).

Mark discipline (Ruling 011.2): a coin's mark is a MARKET PRICE or nothing.
Account cash equity is NEVER substituted for a missing price. With no valid
mark, an account with an open position publishes explicit null equity/PnL and
mark_unavailable=true; a flat account's equity is exact cash regardless of
any mark (qty == 0, so no price enters the calculation)."""
from decimal import Decimal

from . import state, metrics

_NULL_OUTCOME_KEYS = ("equity", "unrealized_pnl", "total_return_pct")


def _account_detail(a, mark):
    lc = a.get("lifecycle")
    inv = None
    if lc is not None:
        inv = {"timeframe": lc["invalidation"]["timeframe"],
               "operator": lc["invalidation"]["operator"],
               "level": str(lc["invalidation"]["level"]),
               "status": ("TRIGGERED" if lc["triggered"] else "NOT TRIGGERED"),
               "triggered_t": (lc["triggered"] or {}).get("t")}
    if not a["qty"]:
        upnl, notional = "0", "0"
    elif mark is None:
        upnl, notional = None, None          # explicit: no valid market mark
    else:
        upnl = str(a["qty"] * (mark - a["entry"])) if a["entry"] else "0"
        notional = str(abs(a["qty"]) * mark)
    return {
        "side": state.side(a),
        # Position entry boundary (Mentor Ruling 5): the ACTIVE lifecycle's
        # start_t — set once when the position opened (a reversal starts a new
        # lifecycle; averaging keeps the original). Derived from existing
        # persisted state; never inferred from the most recent decision.
        "entry_t": (a["lifecycle"]["start_t"]
                    if a["qty"] and a.get("lifecycle") else None),
        "qty": str(abs(a["qty"])),
        "notional": notional,
        "entry": str(a["entry"]) if a["entry"] is not None else None,
        "stop": str(a["stop"]) if a["stop"] is not None else None,
        "tp": str(a["tp"]) if a["tp"] is not None else None,
        "unrealized_pnl": upnl,
        "thesis": (a["theses"][-1]["text"] if a["theses"] else None),
        "invalidation": inv,
        "watch": ({"level": str(a["watch"]["level"]),
                   "timeframe": a["watch"]["timeframe"],
                   "operator": a["watch"]["operator"],
                   "triggered": bool(a["watch"].get("triggered"))}
                  if a.get("watch") else None),
        "trades": a["trades"][-5:],
    }


def _account_row(a, mark):
    if mark is not None:
        out = metrics.account_outcomes(a, mark)
    elif state.side(a) == "flat":
        # equity_at(flat, p) == E for every p: cash is exact without a mark
        out = metrics.account_outcomes(a, Decimal(0))
    else:
        out = metrics.account_outcomes(a, a["entry"])
        for k in _NULL_OUTCOME_KEYS:
            out[k] = None                    # never fabricate from a stale price
    row = dict(out, **_account_detail(a, mark))
    row["mark"] = str(mark) if mark is not None else None
    row["mark_unavailable"] = mark is None
    return row


def payload(accounts, ledger, snapshots, heartbeat, manifest, cfg, marks=None):
    """`marks` (optional): {coin: Decimal|str|None} of PERSISTED boundary
    market marks — the production publisher always supplies these. Without
    `marks`, marks derive from live snapshots (offline/demo use)."""
    if marks is None:
        marks = {c: s["P_T"] for c, s in snapshots.items() if s}
    marks = {c: (Decimal(str(v)) if v is not None else None)
             for c, v in marks.items()}
    per_coin = {}
    for coin in state.COINS:
        mark = marks.get(coin)
        coin_accounts = [a for a in accounts.values() if a["coin"] == coin]
        per_coin[coin] = {
            "accounts": [_account_row(a, mark) for a in coin_accounts],
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
