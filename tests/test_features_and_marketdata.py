"""Feature math (visible-window only) and strict market-data validation."""
from decimal import Decimal

import pytest

from conftest import T0, load_fix
from engine import features, marketdata

HOUR, MIN, DAY = 3600, 60, 86400


def mk(t, o, h, l, c, v):
    return {"t": t, "o": Decimal(str(o)), "h": Decimal(str(h)),
            "l": Decimal(str(l)), "c": Decimal(str(c)), "v": Decimal(str(v))}


def flat_candles(n, px=100, v=5):
    return [mk(i * HOUR, px, px, px, px, v) for i in range(n)]


def test_sma_exact():
    cs = [Decimal(i) for i in range(1, 73)]
    assert features.sma(cs, 20, 2) == "62.50"          # mean of 53..72
    assert features.sma(cs, 50, 2) == "47.50"          # mean of 23..72
    assert features.sma([Decimal(1)] * 10, 20, 2) == "n/a"


def test_rsi_all_gains_and_insufficient():
    up = [Decimal(i) for i in range(1, 30)]
    assert features.rsi14(up) == "100.0"
    assert features.rsi14(up[:10]) == "n/a"


def test_atr_flat_market_zero():
    assert features.atr14(flat_candles(72), 2) == "0.00"


def test_volume_baseline_excludes_latest():
    candles = flat_candles(72, v=10)
    candles[-1]["v"] = Decimal("30")     # latest spikes; baseline must be 10
    cur, base, ratio = features.volume_features(candles, 1)
    assert (cur, base, ratio) == ("30.0", "10.0", "3.00")


def test_volume_ratio_zero_baseline_na():
    candles = flat_candles(72, v=0)
    candles[-1]["v"] = Decimal("5")
    cur, base, ratio = features.volume_features(candles, 1)
    assert ratio == "n/a"


def test_vwap_includes_latest_and_zero_volume_na():
    candles = flat_candles(72, px=100, v=1)
    vwap, pct = features.vwap24(candles, Decimal("101"), 2)
    assert vwap == "100.00" and pct == "+1.00"
    zero = flat_candles(72, v=0)
    assert features.vwap24(zero, Decimal("100"), 2) == ("n/a", "n/a")


def test_features_computed_only_from_visible_window():
    """Same visible 72 candles => same features, regardless of earlier history."""
    a = flat_candles(90, px=100)
    b = [mk(c["t"], 999, 999, 999, 999, 42) for c in a[:18]] + a[18:]
    snapA = {"hourly": a[-72:], "P_T": Decimal("100")}
    snapB = {"hourly": b[-72:], "P_T": Decimal("100")}
    assert features.feature_values(snapA, 2, 1) == features.feature_values(snapB, 2, 1)


def test_snapshot_builds_from_fixtures(snapshots):
    s = snapshots["BTC"]
    assert len(s["hourly"]) == 72 and len(s["daily_closes"]) == 40
    assert s["hourly"][-1]["t"] + HOUR <= T0
    assert s["source"] == "kraken"


def test_hourly_gap_rejected():
    k1h = [dict(t=i * HOUR + 1000000, o=1, h=1, l=1, c=1, v=1) for i in range(80)]
    del k1h[70]
    with pytest.raises(marketdata.DataUnavailable):
        marketdata.build_snapshot("BTC", [dict(t=1000000 + 79 * HOUR, o=1, h=1, l=1, c=1, v=1)],
                                  k1h, [dict(t=i * DAY, o=1, h=1, l=1, c=1, v=1)
                                        for i in range(45)], 1000000 + 80 * HOUR)


def test_short_hourly_history_rejected():
    with pytest.raises(marketdata.DataUnavailable):
        marketdata.build_snapshot("BTC",
                                  [dict(t=0, o=1, h=1, l=1, c=1, v=1)],
                                  [dict(t=i * HOUR, o=1, h=1, l=1, c=1, v=1) for i in range(10)],
                                  [dict(t=i * DAY, o=1, h=1, l=1, c=1, v=1) for i in range(45)],
                                  100 * HOUR)


def test_daily_excludes_incomplete_current_day(snapshots):
    """Ruling 004.2: the incomplete current UTC day is never supplied."""
    day_start = (T0 // DAY) * DAY
    k1d = load_fix("BTC", "1d")
    last_supplied_close = snapshots["BTC"]["daily_closes"][-1]
    completed = [c for c in k1d if c["t"] + DAY <= day_start]
    assert str(last_supplied_close) == str(Decimal(str(completed[-1]["c"])))


def test_1m_coverage_gap_rejected():
    candles = [mk(i * MIN, 1, 1, 1, 1, 1) for i in range(100)]
    del candles[50]
    with pytest.raises(marketdata.DataUnavailable):
        marketdata.validate_1m_coverage(candles, 0, 100 * MIN)


def test_execution_price_is_last_completed_1m_close(snapshots):
    k1m = load_fix("BTC", "1m")
    last = [c for c in k1m if c["t"] + MIN <= T0][-1]
    assert snapshots["BTC"]["P_T"] == Decimal(str(last["c"]))
