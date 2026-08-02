"""1-minute candle replay implementing the deterministic event-ordering contract."""
from decimal import Decimal

from . import state, lifecycle, execution

HOUR, MIN = 3600, 60


def _protective(acct):
    """(level, reason) for the position's protective exit, or (None, None)."""
    liq = state.liq_threshold(acct)
    stop = acct["stop"]
    if acct["qty"] > 0:
        if stop is not None and (liq is None or stop >= liq):
            return stop, "stop_loss"
        return liq, "liquidation"
    if acct["qty"] < 0:
        if stop is not None and (liq is None or stop <= liq):
            return stop, "stop_loss"
        return liq, "liquidation"
    return None, None


def replay(accounts, candles_1m, records):
    """Replay candles chronologically for one coin's accounts.

    records: list collecting event dicts (AMBIGUOUS_CANDLE_PROTECTIVE_FIRST,
    SAME_CANDLE_INVALIDATION_AND_EXIT, exits, latches).
    """
    for c in candles_1m:
        t_open, o, h, l = c["t"], c["o"], c["h"], c["l"]
        hour_close_t = t_open + MIN if (t_open + MIN) % HOUR == 0 else None
        for acct in accounts:
            lc = acct["lifecycle"]
            eligible = lc is not None and lifecycle.active_at(lc, t_open)
            exited = False
            if acct["qty"] != 0 and eligible:
                prot, prot_reason = _protective(acct)
                long = acct["qty"] > 0
                # A. opening-gap evaluation: liquidation, then stop, then tp
                if state.is_liquidatable(acct, o):
                    execution.close_position(acct, o, "liquidation", t_open)
                    records.append({"e": "exit", "id": acct["id"], "t": t_open,
                                    "reason": "liquidation", "price": str(o), "gap": True})
                    exited = True
                elif acct["stop"] is not None and ((long and o <= acct["stop"]) or
                                                   (not long and o >= acct["stop"])):
                    execution.close_position(acct, o, "stop_loss", t_open)
                    records.append({"e": "exit", "id": acct["id"], "t": t_open,
                                    "reason": "stop_loss", "price": str(o), "gap": True})
                    exited = True
                elif acct["tp"] is not None and ((long and o >= acct["tp"]) or
                                                 (not long and o <= acct["tp"])):
                    execution.close_position(acct, o, "take_profit", t_open)
                    records.append({"e": "exit", "id": acct["id"], "t": t_open,
                                    "reason": "take_profit", "price": str(o), "gap": True})
                    exited = True
                else:
                    # B. intracandle: protective vs take-profit
                    prot_hit = prot is not None and ((long and l <= prot) or
                                                     (not long and h >= prot))
                    tp_hit = acct["tp"] is not None and ((long and h >= acct["tp"]) or
                                                         (not long and l <= acct["tp"]))
                    if prot_hit:
                        if tp_hit:
                            records.append({"e": "AMBIGUOUS_CANDLE_PROTECTIVE_FIRST",
                                            "id": acct["id"], "t": t_open})
                        execution.close_position(acct, prot, prot_reason, t_open)
                        records.append({"e": "exit", "id": acct["id"], "t": t_open,
                                        "reason": prot_reason, "price": str(prot)})
                        exited = True
                    elif tp_hit:
                        execution.close_position(acct, acct["tp"], "take_profit", t_open)
                        records.append({"e": "exit", "id": acct["id"], "t": t_open,
                                        "reason": "take_profit",
                                        "price": str(acct["trades"][-1]["exit"])})
                        exited = True
            # C. invalidation observation (lifecycle existed at candle open)
            if eligible and lc["triggered"] is None \
                    and lc["invalidation"]["timeframe"] == "1m_intrabar" \
                    and lifecycle.condition_met(lc["invalidation"], l, h):
                lifecycle.latch(lc, t_open, t_open)
                records.append({"e": "invalidation_latched", "id": acct["id"], "t": t_open})
                if exited:
                    lc["records"].append("SAME_CANDLE_INVALIDATION_AND_EXIT")
                    records.append({"e": "SAME_CANDLE_INVALIDATION_AND_EXIT",
                                    "id": acct["id"], "t": t_open})
            # watch conditions (informational)
            wc = acct.get("watch")
            if wc and not wc.get("triggered") and acct["qty"] == 0:
                if lifecycle.condition_met(wc, l, h):
                    wc["triggered"] = {"t": t_open, "price": str(wc["level"])}
        # 1h_close invalidation: only at completed hourly closes; lifecycle must
        # still be open at that close (D: ended lifecycles never trigger)
        if hour_close_t is not None:
            for acct in accounts:
                lc = acct["lifecycle"]
                if (lc is not None and lc["triggered"] is None
                        and lc["ended_t"] is None
                        and lc["invalidation"]["timeframe"] == "1h_close"
                        and lc["start_t"] <= hour_close_t - HOUR):
                    close_px = c["c"]
                    if lifecycle.condition_met(lc["invalidation"], close_px, close_px):
                        lifecycle.latch(lc, hour_close_t, t_open)
                        records.append({"e": "invalidation_latched", "id": acct["id"],
                                        "t": hour_close_t, "tf": "1h_close"})
    return records
