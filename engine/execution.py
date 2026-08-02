"""Decision execution and Decimal accounting. Fills at the frozen price only."""
from decimal import Decimal, ROUND_DOWN

from . import state, lifecycle

QTY_DP = state.QTY_DECIMALS   # authoritative: frozen config (Ruling 008.14)


def _qq(coin, qty):
    return qty.quantize(Decimal(1).scaleb(-QTY_DP[coin]), rounding=ROUND_DOWN)


def _fee(notional):
    return (abs(notional) * state.FEE_RATE)


def _record_trade(acct, exit_price, closed_qty, reason, t):
    pnl = closed_qty * (exit_price - acct["entry"])
    fee = _fee(closed_qty * exit_price)
    acct["E"] += pnl - fee
    acct["fees_total"] += fee
    acct["trades"].append({
        "lifecycle_id": (acct["lifecycle"] or {}).get("lifecycle_id"),
        "side": "long" if closed_qty > 0 else "short",
        "qty": str(abs(closed_qty)), "entry": str(acct["entry"]),
        "exit": str(exit_price), "pnl": str(pnl - fee), "fee": str(fee),
        "reason": reason, "closed_ts": t,
    })
    return pnl - fee


def close_position(acct, price, reason, t):
    """Full close (decision, stop, tp, or liquidation). Fee charged on every exit."""
    if acct["qty"] == 0:
        return
    _record_trade(acct, price, acct["qty"], reason, t)
    acct["qty"] = Decimal("0")
    acct["entry"] = None
    acct["stop"] = None
    acct["tp"] = None
    lifecycle.end(acct["lifecycle"], t, reason)
    acct["lifecycle"] = None            # history stays in acct["lifecycles"]
    acct["active_lifecycle_id"] = None
    if acct["E"] <= 0:
        acct["E"] = Decimal("0")
        acct["terminal"] = True
        acct["terminal_info"] = {"t": t, "cause": reason}


def _open(acct, sidename, size_usd, price, t, inv):
    qty = _qq(acct["coin"], size_usd / price)
    if sidename == "short":
        qty = -qty
    fee = _fee(abs(qty) * price)
    acct["E"] -= fee
    acct["fees_total"] += fee
    acct["qty"] = qty
    acct["entry"] = price
    lc_id = f"{acct['id']}-L{len(acct['lifecycles']) + 1}"
    lc = lifecycle.new_lifecycle(t, inv, lifecycle_id=lc_id)
    acct["lifecycles"].append(lc)       # append-only history (Ruling 008.15)
    acct["lifecycle"] = lc
    acct["active_lifecycle_id"] = lc_id
    return qty


def apply_decision(acct, dec, p_t, t):
    """Apply a semantically valid decision at frozen P_T. Returns action log list."""
    log = []
    pos = dec["position"]
    cur = state.side(acct)
    size = Decimal(str(dec.get("size_usd") or 0))

    if pos == "flat":
        if cur != "flat":
            close_position(acct, p_t, "decision_close", t)
            log.append("closed")
        else:
            log.append("held_flat")
        acct["watch"] = (dict(dec["watch_condition"], triggered=None)
                        if dec.get("watch_condition") else None)
        if acct["watch"]:
            acct["watch"]["level"] = Decimal(str(acct["watch"]["level"]))
        return log

    if cur != "flat" and pos != cur:  # reversal: two legs, two fees, new lifecycle
        close_position(acct, p_t, "decision_flip", t)
        log.append("reversal_close")
        if acct["terminal"]:
            return log
        _open(acct, pos, size, p_t, t, dec["invalidation"])
        log.append("reversal_open")
    elif cur == "flat":
        _open(acct, pos, size, p_t, t, dec["invalidation"])
        log.append("opened")
    else:  # same-side exact target, $10 minimum delta, no churn guard
        target_qty = _qq(acct["coin"], size / p_t) * (1 if pos == "long" else -1)
        delta = target_qty - acct["qty"]
        delta_notional = abs(delta) * p_t
        if delta_notional < state.MIN_DELTA_USD:
            log.append("NO_EXECUTION_BELOW_MINIMUM")
        elif abs(target_qty) > abs(acct["qty"]):
            fee = _fee(delta_notional)
            acct["entry"] = ((abs(acct["qty"]) * acct["entry"] + abs(delta) * p_t)
                             / abs(target_qty))
            acct["E"] -= fee
            acct["fees_total"] += fee
            acct["qty"] = target_qty
            log.append("increased")
        else:
            reduced = acct["qty"] - target_qty
            pnl = reduced * (p_t - acct["entry"])
            fee = _fee(delta_notional)
            acct["E"] += pnl - fee
            acct["fees_total"] += fee
            acct["qty"] = target_qty
            log.append("reduced")

    if acct["qty"] != 0:
        # null REMOVES the resting level (explicit contract)
        acct["stop"] = Decimal(str(dec["stop_loss"])) if dec.get("stop_loss") is not None else None
        acct["tp"] = Decimal(str(dec["take_profit"])) if dec.get("take_profit") is not None else None
        acct["watch"] = None
    inv, lc = dec.get("invalidation"), acct["lifecycle"]
    if inv and lc and lc["invalidation"].get("level") is not None:
        lc["invalidation"]["level"] = Decimal(str(lc["invalidation"]["level"]))
    return log
