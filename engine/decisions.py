"""Semantic decision validation. Returns the fixed rejection strings (blocks.md)."""
from decimal import Decimal

from . import state
from .lifecycle import OPERATORS, TIMEFRAMES

LEVEL_LO, LEVEL_HI = Decimal("0.2"), Decimal("5")


def _num(v):
    try:
        d = Decimal(str(v))
        return d if d.is_finite() else None
    except Exception:
        return None


def _cond_ok(cond):
    return (isinstance(cond, dict)
            and cond.get("timeframe") in TIMEFRAMES
            and cond.get("operator") in OPERATORS
            and _num(cond.get("level")) is not None)


def validate(acct, dec, p_t):
    """Return [] if valid, else the exact fixed rejection strings."""
    r = []
    pos = dec.get("position")
    size = _num(dec.get("size_usd"))
    eq = state.equity_at(acct, p_t)
    max_notional = state.MAX_LEVERAGE * eq
    cur = state.side(acct)
    holding_same_lifecycle = (cur != "flat" and pos == cur)
    opening = (cur == "flat" and pos in ("long", "short"))
    reversing = (cur != "flat" and pos in ("long", "short") and pos != cur)

    if size is None or (size is not None and size < 0):
        r.append("size_usd must be a finite, non-negative number")
        size = None
    if pos == "flat" and size is not None and size != 0:
        r.append("size_usd must be 0 when position is flat")
    if pos in ("long", "short") and size is not None and size < Decimal("10"):
        r.append("size_usd must be at least 10 when opening or holding a position")
    # Leverage semantics (Ruling 006.1): the 5x cap governs NEW or INCREASED
    # exposure only. Exact holds and reductions are always allowed, even when
    # mark-to-market losses have pushed effective leverage above 5x.
    if pos in ("long", "short") and size is not None:
        current_notional = abs(acct["qty"]) * p_t
        # Exact delta semantics (Ruling 007): |delta| < $10 is NO_EXECUTION
        # (neither increase nor reduction); delta >= +$10 is an executable
        # increase and MUST pass the 5x new/increased-exposure limit;
        # delta <= -$10 is an executable reduction, always allowed.
        delta_notional = size - current_notional
        increasing = (cur == "flat" or pos != cur
                      or delta_notional >= Decimal("10"))
        if increasing and size > max_notional:
            r.append(f"target notional {size} exceeds the 5x leverage limit "
                     f"({max_notional}) — TARGET_EXCEEDS_MAX_LEVERAGE")

    inv, wc = dec.get("invalidation"), dec.get("watch_condition")
    if (opening or reversing) and (inv is None or not _cond_ok(inv)):
        r.append("an invalidation is required when opening or reversing a position")
    if holding_same_lifecycle and inv is not None:
        r.append("this position already has an immutable invalidation; invalidation "
                 "must be null when holding, increasing, or reducing")
    if pos == "flat" and inv is not None:
        r.append("invalidation must be null when your decision leaves you flat")
    if pos in ("long", "short") and wc is not None:
        r.append("watch_condition must be null when your decision leaves you holding a position")

    stop, tp = _num(dec.get("stop_loss")), _num(dec.get("take_profit"))
    if pos == "long":
        if dec.get("stop_loss") is not None and (stop is None or stop >= p_t):
            r.append("stop_loss must be below the execution price for a long position")
        if dec.get("take_profit") is not None and (tp is None or tp <= p_t):
            r.append("take_profit must be above the execution price for a long position")
    if pos == "short":
        if dec.get("stop_loss") is not None and (stop is None or stop <= p_t):
            r.append("stop_loss must be above the execution price for a short position")
        if dec.get("take_profit") is not None and (tp is None or tp >= p_t):
            r.append("take_profit must be below the execution price for a short position")

    for cond, name in ((inv, "invalidation"), (wc, "watch_condition")):
        if cond is not None and _cond_ok(cond):
            lvl = _num(cond["level"])
            if not (LEVEL_LO * p_t <= lvl <= LEVEL_HI * p_t):
                r.append(f"{name} level {cond['level']} is outside the accepted "
                         f"range (0.2x to 5x the execution price)")
    return r


def retry_message(reasons):
    return (f"Your previous decision was rejected: {'; '.join(reasons)}. "
            "The market snapshot and your account are unchanged. Submit a "
            "corrected decision using the submit_decision tool.")
