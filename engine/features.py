"""Neutral Feature-arm calculations. Eight feature definitions, nine displayed
values. Computed ONLY from the visible snapshot candles. No interpretation."""
from decimal import Decimal, ROUND_HALF_UP

NA = "n/a"


def _q(v, places):
    return str(v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def rsi14(closes):
    n = 14
    if len(closes) < n + 1:
        return NA
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    ag = sum((max(d, Decimal(0)) for d in diffs[:n]), Decimal(0)) / n
    al = sum((max(-d, Decimal(0)) for d in diffs[:n]), Decimal(0)) / n
    for d in diffs[n:]:
        ag = (ag * (n - 1) + max(d, Decimal(0))) / n
        al = (al * (n - 1) + max(-d, Decimal(0))) / n
    if al == 0:
        return "100.0"
    rsi = Decimal(100) - Decimal(100) / (1 + ag / al)
    return _q(rsi, 1)


def sma(closes, n, price_dp):
    if len(closes) < n:
        return NA
    return _q(sum(closes[-n:], Decimal(0)) / n, price_dp)


def atr14(candles, price_dp):
    n = 14
    if len(candles) < n + 1:
        return NA
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n], Decimal(0)) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return _q(a, price_dp)


def volume_features(candles, vol_dp):
    """(latest volume, prev-24 mean excluding latest, ratio)."""
    if len(candles) < 25:
        return NA, NA, NA
    latest = candles[-1]["v"]
    prev24 = [c["v"] for c in candles[-25:-1]]
    mean = sum(prev24, Decimal(0)) / 24
    cur = _q(latest, vol_dp)
    base = _q(mean, vol_dp)
    ratio = NA if mean == 0 else _q(latest / mean, 2)
    return cur, base, ratio


def vwap24(candles, p_t, price_dp):
    """(VWAP over latest 24 candles incl. latest, price-minus-VWAP % signed)."""
    if len(candles) < 24:
        return NA, NA
    win = candles[-24:]
    tv = sum((c["v"] for c in win), Decimal(0))
    if tv == 0:
        return NA, NA
    num = sum((((c["h"] + c["l"] + c["c"]) / 3) * c["v"] for c in win), Decimal(0))
    vwap = num / tv
    pct = (p_t - vwap) / vwap * 100
    pct_s = _q(pct, 2)
    if not pct_s.startswith("-"):
        pct_s = "+" + pct_s
    return _q(vwap, price_dp), pct_s


def feature_values(snapshot, price_dp, vol_dp):
    """All nine displayed values keyed by placeholder name (no braces)."""
    h = snapshot["hourly"]
    closes = [c["c"] for c in h]
    cur, base, ratio = volume_features(h, vol_dp)
    vwap, pct = vwap24(h, snapshot["P_T"], price_dp)
    return {
        "RSI14_H": rsi14(closes),
        "SMA20_H": sma(closes, 20, price_dp),
        "SMA50_H": sma(closes, 50, price_dp),
        "ATR14_H": atr14(h, price_dp),
        "CURRENT_VOLUME_H": cur,
        "MEAN_VOLUME_PREV24_H": base,
        "VOLUME_RATIO_24": ratio,
        "VWAP24_H": vwap,
        "PRICE_MINUS_VWAP_PCT": pct,
    }
