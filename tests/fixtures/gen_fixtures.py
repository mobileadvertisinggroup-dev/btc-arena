"""Deterministic fixture generator (seeded; run once, output committed)."""
import json
import os
import random

HOUR, MIN, DAY = 3600, 60, 86400
T0 = 1754870400  # 2025-08-11T00:00:00Z — synthetic fixture boundary (epoch is arbitrary)
BASES = {"BTC": 63000.0, "ETH": 3100.0, "SOL": 150.0}


def gen_series(coin, seed):
    """Seeded walk. SOL carries a deterministic stress segment for the season:
    -0.05%/min drift from T0+30h to T0+40h and a single -30% gap candle at
    exactly T0+35h — engineered so a 5x long opened at T0+29h gaps through its
    liquidation threshold to terminal zero (synthetic stress, documented)."""
    rng = random.Random(seed)
    px = BASES[coin]
    out_1m, out_1h, out_1d = [], [], []
    start = T0 - 45 * DAY
    t = start
    minute_prices = []
    while t < T0 + 73 * HOUR:
        drift = rng.gauss(0, px * 0.0006)
        if coin == "SOL" and T0 + 30 * HOUR <= t < T0 + 40 * HOUR:
            drift -= px * 0.0005
            if t == T0 + 35 * HOUR:
                px *= 0.70
        o = px
        c = max(px + drift, px * 0.05)
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.0004)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.0004)))
        v = abs(rng.gauss(8, 3))
        row = {"t": t, "o": round(o, 2), "h": round(h, 2),
               "l": round(l, 2), "c": round(c, 2), "v": round(v, 2)}
        minute_prices.append(row)
        if t >= T0 - 12 * HOUR:      # keep 1m where replay needs it (through season)
            out_1m.append(row)
        px = c
        t += MIN
    # aggregate 1h and 1d from the same walk (consistent OHLC)
    def agg(rows, step):
        bucket = {}
        for r in rows:
            b = (r["t"] // step) * step
            if b not in bucket:
                bucket[b] = {"t": b, "o": r["o"], "h": r["h"], "l": r["l"],
                             "c": r["c"], "v": 0.0}
            k = bucket[b]
            k["h"] = max(k["h"], r["h"])
            k["l"] = min(k["l"], r["l"])
            k["c"] = r["c"]
            k["v"] = round(k["v"] + r["v"], 2)
        return [bucket[b] for b in sorted(bucket)]
    end = T0 + 73 * HOUR
    out_1h = [c for c in agg(minute_prices, HOUR) if c["t"] + HOUR <= end]
    out_1d = [c for c in agg(minute_prices, DAY) if c["t"] + DAY <= end]
    return out_1m, out_1h, out_1d


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    for i, coin in enumerate(("BTC", "ETH", "SOL")):
        m1, h1, d1 = gen_series(coin, seed=1000 + i)
        for name, data in (("1m", m1), ("1h", h1), ("1d", d1)):
            with open(os.path.join(here, f"{coin.lower()}_{name}.json"), "w") as f:
                json.dump(data, f)
        print(coin, len(m1), "1m,", len(h1), "1h,", len(d1), "1d")


if __name__ == "__main__":
    main()
