"""Deterministic prompt rendering from the canonical templates and blocks."""
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from . import config, features, state

PLACEHOLDER_RE = re.compile(r"\{[A-Z0-9_]+\}")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _p(v, dp=2):
    return str(Decimal(v).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP))


def round_id(coin, T):
    return f"v1-{coin}-{_iso(T)}"


def position_block(acct, price_dp):
    if acct["qty"] == 0:
        return "flat — no position"
    s = state.side(acct)
    notional = abs(acct["qty"]) * acct["_mark"]
    upnl = acct["qty"] * (acct["_mark"] - acct["entry"])
    stop = _p(acct["stop"], price_dp) if acct["stop"] is not None else "none"
    tp = _p(acct["tp"], price_dp) if acct["tp"] is not None else "none"
    liq = state.liq_threshold(acct)
    return (f"{s} {acct['qty'].copy_abs()} {acct['coin']} (${_p(notional, 0)} notional) | "
            f"entry {_p(acct['entry'], price_dp)} | unrealized P&L {'+' if upnl >= 0 else ''}{_p(upnl, 2)} | "
            f"stop-loss {stop} | take-profit {tp} | approx. liquidation price {_p(liq, price_dp)}")


def trades_block(acct, price_dp):
    if not acct["trades"]:
        return "Closed trades: none yet."
    rows = [f"Closed trades ({len(acct['trades'])} total, most recent 5 shown, oldest first):"]
    for tr in acct["trades"][-5:]:
        rows.append(f"{tr['side']} | entry {tr['entry']} | exit {tr['exit']} | "
                    f"P&L {'+' if Decimal(tr['pnl']) >= 0 else ''}{tr['pnl']} | "
                    f"closed {tr['closed_ts']} | reason {tr['reason']}")
    return "\n".join(rows)


def _cond_words(inv):
    op = "below" if inv["operator"] == "price_at_or_below" else "above"
    tf = ("1h close at or " + op) if inv["timeframe"] == "1h_close" \
        else ("price trades at or " + op)
    return tf


def condition_block(acct, price_dp):
    lc = acct["lifecycle"]
    if acct["qty"] != 0 and lc is not None:
        inv = lc["invalidation"]
        head = (f"Your invalidation for this position (set when the position was opened; "
                f"immutable for its lifecycle): {acct['coin']} {_cond_words(inv)} "
                f"{_p(inv['level'], price_dp)}.")
        if lc["triggered"] is None:
            return (head + " Status: NOT TRIGGERED as of this round. Do not submit a "
                    "new invalidation while this position remains open.")
        tr = lc["triggered"]
        return (head + f" Status: TRIGGERED at {_iso(tr['t'])} (price {tr['price']}). "
                "This record is permanent. Your thesis was invalidated by your own "
                "stated condition; decide and explain how you respond.")
    wc = acct.get("watch")
    if wc:
        status = "NOT TRIGGERED as of this round" if not wc.get("triggered") else \
            f"TRIGGERED at {_iso(wc['triggered']['t'])} (price {wc['triggered']['price']})"
        return (f"Your watch condition (informational, not scored): {acct['coin']} "
                f"{_cond_words(wc)} {_p(wc['level'], price_dp)}. Status: {status}.")
    return "No watch condition set."


def memory_block(acct):
    if not acct["theses"]:
        return "This is your first round. You have no prior notes."
    rows = ["Your notes from previous rounds (oldest first):"]
    rows += [f"[{th['t']}] {th['text']}" for th in acct["theses"][-3:]]
    return "\n".join(rows)


def hourly_rows(snapshot, price_dp, vol_dp):
    out = []
    for c in snapshot["hourly"]:
        ts = datetime.fromtimestamp(c["t"], timezone.utc).strftime("%m-%d %H:%M")
        out.append(f"{ts} {_p(c['o'], price_dp)} {_p(c['h'], price_dp)} "
                   f"{_p(c['l'], price_dp)} {_p(c['c'], price_dp)} {_p(c['v'], vol_dp)}")
    return "\n".join(out)


def render(acct, snapshot, cfg):
    """Render (system_prompt, user_prompt) for one account from frozen data."""
    coin_cfg = cfg["coins"][acct["coin"]]
    pdp, vdp = coin_cfg["price_decimals"], coin_cfg["volume_decimals"]
    acct["_mark"] = snapshot["P_T"]
    tpl = config.load_text("prompts/v1/user_feature.txt" if acct["arm"] == "ta"
                           else "prompts/v1/user_raw.txt")
    subs = {
        "{COIN}": acct["coin"],
        "{ROUND_ID}": round_id(acct["coin"], snapshot["T"]),
        "{ROUND_TS}": _iso(snapshot["T"]),
        "{PRICE}": _p(snapshot["P_T"], pdp),
        "{HOURLY_ROWS}": hourly_rows(snapshot, pdp, vdp),
        "{DAILY_CLOSES}": " ".join(_p(v, pdp) for v in snapshot["daily_closes"]),
        "{EQUITY}": _p(state.equity_at(acct, snapshot["P_T"]), 2),
        "{POSITION_BLOCK}": position_block(acct, pdp),
        "{FEES_TOTAL}": _p(acct["fees_total"], 2),
        "{MAX_NOTIONAL}": _p(state.MAX_LEVERAGE * state.equity_at(acct, snapshot["P_T"]), 0),
        "{TRADES_BLOCK}": trades_block(acct, pdp),
        "{CONDITION_BLOCK}": condition_block(acct, pdp),
        "{MEMORY_BLOCK}": memory_block(acct),
    }
    if acct["arm"] == "ta":
        subs.update({"{" + k + "}": v for k, v in
                     features.feature_values(snapshot, pdp, vdp).items()})
    user = tpl
    for k, v in subs.items():
        user = user.replace(k, str(v))
    leftover = PLACEHOLDER_RE.findall(user)
    if leftover:
        raise ValueError(f"unrendered placeholders: {leftover}")
    system = config.load_text("prompts/v1/system.txt").replace("{COIN}", acct["coin"])
    return system, user
