"""Immutable position-lifecycle invalidation: created once, latched forever."""

OPERATORS = ("price_at_or_below", "price_at_or_above")
TIMEFRAMES = ("1h_close", "1m_intrabar")


def new_lifecycle(start_t, invalidation):
    """A lifecycle begins exactly at boundary T (ruling 004.3)."""
    return {
        "start_t": start_t,           # first eligible 1m candle: open >= start_t
        "invalidation": dict(invalidation),  # immutable copy
        "triggered": None,            # {"t","price","candle_t"} latched permanently
        "ended_t": None,
        "end_reason": None,
        "post_trigger_action": None,  # closed|reduced|held|increased|reversed
        "records": [],                # SAME_CANDLE_INVALIDATION_AND_EXIT etc.
    }


def condition_met(inv, low, high):
    if inv["operator"] == "price_at_or_below":
        return low <= inv["level"]
    return high >= inv["level"]


def latch(lc, t, candle_t):
    """First trigger only; permanent."""
    if lc["triggered"] is None:
        lc["triggered"] = {"t": t, "price": str(lc["invalidation"]["level"]),
                           "candle_t": candle_t}
        return True
    return False


def end(lc, t, reason):
    if lc is not None and lc["ended_t"] is None:
        lc["ended_t"] = t
        lc["end_reason"] = reason


def active_at(lc, candle_open_t):
    """Lifecycle exists at a candle's open: opened at/before it, not yet ended."""
    if lc is None:
        return False
    if candle_open_t < lc["start_t"]:
        return False
    return lc["ended_t"] is None or lc["ended_t"] > candle_open_t
