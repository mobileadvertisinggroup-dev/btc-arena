"""Market snapshot construction and strict completeness validation (Kraken-only).

Candles are dicts {"t": int unix open-time, "o","h","l","c","v": Decimal}.
"""
from decimal import Decimal

H72, D40 = 72, 40
HOUR, MIN, DAY = 3600, 60, 86400


class DataUnavailable(Exception):
    """Any gap / staleness / unclosed candle => affected pairs PAIR_ABORTED."""


def to_dec(candles):
    return [{"t": int(c["t"]), **{k: Decimal(str(c[k])) for k in "ohlcv"}}
            for c in candles]


def _check_contiguous(candles, step, n, label):
    if len(candles) != n:
        raise DataUnavailable(f"{label}: need exactly {n} candles, got {len(candles)}")
    for a, b in zip(candles, candles[1:]):
        if b["t"] - a["t"] != step:
            raise DataUnavailable(f"{label}: gap between {a['t']} and {b['t']}")


def _check_closed(candles, step, now_t, label):
    if candles and candles[-1]["t"] + step > now_t:
        raise DataUnavailable(f"{label}: final candle not fully closed")


def _check_fresh(candles, step, required_last_open, label):
    if not candles or candles[-1]["t"] != required_last_open:
        got = candles[-1]["t"] if candles else None
        raise DataUnavailable(f"{label}: stale — last open {got}, "
                              f"required {required_last_open}")


def _check_sane(candles, step, label):
    prev = None
    for c in candles:
        if c["t"] % step != 0:
            raise DataUnavailable(f"{label}: timestamp {c['t']} not aligned")
        if prev is not None and c["t"] <= prev:
            raise DataUnavailable(f"{label}: timestamps not strictly increasing")
        prev = c["t"]
        for k in "ohlc":
            if not c[k].is_finite() or c[k] <= 0:
                raise DataUnavailable(f"{label}: non-finite/non-positive price")
        if not c["v"].is_finite() or c["v"] < 0:
            raise DataUnavailable(f"{label}: bad volume")
        if c["h"] < max(c["o"], c["c"], c["l"]) or                 c["l"] > min(c["o"], c["c"], c["h"]):
            raise DataUnavailable(f"{label}: OHLC sanity violation at {c['t']}")


def validate_candle_values(c, label="1m"):
    """STRICT single-candle value validation (Mentor Ruling 019.3): aligned
    timestamp; finite positive O/H/L/C; H >= O,C,L; L <= O,C,H; finite
    non-negative volume. A malformed candle must never execute a stop,
    target, liquidation or invalidation — callers turn this into
    DATA_UNAVAILABLE / CATCHUP_REQUIRED."""
    if c["t"] % MIN != 0:
        raise DataUnavailable(f"{label}: timestamp {c['t']} not aligned")
    for k in "ohlc":
        if not c[k].is_finite() or c[k] <= 0:
            raise DataUnavailable(
                f"{label}: non-finite/non-positive price at {c['t']}")
    if not c["v"].is_finite() or c["v"] < 0:
        raise DataUnavailable(f"{label}: bad volume at {c['t']}")
    if c["h"] < max(c["o"], c["c"], c["l"]) \
            or c["l"] > min(c["o"], c["c"], c["h"]):
        raise DataUnavailable(f"{label}: OHLC sanity violation at {c['t']}")
    return True


def validate_1m_coverage(candles, start_t, end_t):
    """Complete contiguous 1m coverage for [start_t, end_t) PLUS strict
    per-candle value validation (Ruling 019.3); raises on any gap, duplicate,
    misalignment or malformed value."""
    want = list(range(start_t, end_t, MIN))
    have = [c["t"] for c in candles if start_t <= c["t"] < end_t]
    if have != want:
        missing = sorted(set(want) - set(have))[:3]
        raise DataUnavailable(f"1m gap: missing {len(set(want)-set(have))} candles, first {missing}")
    out = [c for c in candles if start_t <= c["t"] < end_t]
    for c in out:
        validate_candle_values(c)
    return out


def build_snapshot(coin, k1m, k1h, k1d, T):
    """Freeze one coin's market snapshot at boundary T. All inputs Kraken-only."""
    k1m, k1h, k1d = to_dec(k1m), to_dec(k1h), to_dec(k1d)
    hourly = [c for c in k1h if c["t"] + HOUR <= T][-H72:]
    _check_contiguous(hourly, HOUR, H72, "hourly")
    _check_closed(hourly, HOUR, T, "hourly")
    _check_fresh(hourly, HOUR, T - HOUR, "hourly")
    _check_sane(hourly, HOUR, "hourly")
    # Daily convention (ruling 004.2): 40 most recent FULLY COMPLETED UTC daily
    # candles; the incomplete current UTC day is never supplied.
    day_start = (T // DAY) * DAY
    daily = [c for c in k1d if c["t"] + DAY <= day_start][-D40:]
    _check_contiguous(daily, DAY, D40, "daily")
    _check_fresh(daily, DAY, day_start - DAY, "daily")
    _check_sane(daily, DAY, "daily")
    m1 = [c for c in k1m if c["t"] + MIN <= T]
    _check_fresh(m1, MIN, T - MIN, "1m")
    # STRICT P_T SOURCE VALIDATION (Mentor Ruling 020.1): the exact T-60
    # candle must be unambiguous (no duplicate) and pass full value
    # validation BEFORE its close may become P_T — a malformed price can
    # never reach prompts, decisions, execution, equity or the dashboard.
    finals = [c for c in m1 if c["t"] == T - MIN]
    if len(finals) != 1:
        raise DataUnavailable(
            f"1m P_T: {len(finals)} candles at T-60 — ambiguous/absent")
    validate_candle_values(finals[0], "1m P_T")
    p_t = finals[0]["c"]
    return {"coin": coin, "T": T, "P_T": p_t, "hourly": hourly,
            "daily_closes": [c["c"] for c in daily], "source": "kraken"}
