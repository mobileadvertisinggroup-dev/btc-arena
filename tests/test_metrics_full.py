"""Ruling 006.5: every primary denominator, exclusion, and compliance rule."""
from decimal import Decimal

from engine import metrics


def row(rid, model, arm, pos, size, eq="10000", stop=None, tp=None):
    return {"round_id": rid, "coin": "btc", "model": model, "arm": arm,
            "position": pos, "size_usd": size, "pre_equity": eq,
            "stop": stop, "tp": tp}


def test_direction_disagreement_and_conflicts():
    log = [row("r1", "haiku", "raw", "long", 1000), row("r1", "haiku", "ta", "short", 1000),
           row("r2", "haiku", "raw", "flat", 0), row("r2", "haiku", "ta", "flat", 0),
           row("r3", "haiku", "raw", "long", 1000), row("r3", "haiku", "ta", "flat", 0)]
    pb = metrics.paired_behaviour(log)
    assert pb["n_paired_rounds"] == 3
    assert pb["direction_disagreement_rate"] == round(2 / 3, 4)
    assert pb["direction_conflicts"] == 1


def test_size_difference_in_equity_multiples():
    log = [row("r1", "opus", "raw", "long", 20000, eq="10000"),
           row("r1", "opus", "ta", "long", 5000, eq="10000")]
    pb = metrics.paired_behaviour(log)
    assert pb["mean_abs_size_diff_equity_multiples"] == "1.5"


def test_stop_and_target_usage_denominator_positioned_only():
    log = [row("r1", "s", "raw", "long", 1000, stop=95, tp=110),
           row("r1", "s", "ta", "flat", 0),
           row("r2", "s", "raw", "long", 1000), row("r2", "s", "ta", "long", 1000, stop=90)]
    pb = metrics.paired_behaviour(log)
    assert pb["stop_usage"]["raw"] == 0.5      # 1 of 2 positioned raw rounds
    assert pb["stop_usage"]["ta"] == 1.0       # 1 of 1 positioned ta round
    assert pb["tp_usage"]["raw"] == 0.5 and pb["tp_usage"]["ta"] == 0.0


def test_committed_only_and_exclusions():
    ledger = [{"status": "PAIR_COMMITTED"}, {"status": "PAIR_ABORTED"},
              {"status": "ROUND_SKIPPED"}, {"status": "PAIR_TERMINAL_SPLIT"},
              {"status": "PAIR_COMMITTED"}]
    assert len(metrics.committed_only(ledger)) == 2
    exc = metrics.excluded_counts(ledger)
    assert exc == {"PAIR_ABORTED": 1, "ROUND_SKIPPED": 1, "PAIR_TERMINAL_SPLIT": 1}


def test_turnover_time_weighted():
    series = [(0, Decimal("10000")), (100, Decimal("20000")), (200, Decimal("20000"))]
    twa = metrics.time_weighted_avg(series)
    assert twa == Decimal("15000")
    assert metrics.turnover(Decimal("30000"), twa) == Decimal("2")
    assert metrics.turnover(Decimal("30000"), Decimal("0")) is None
    assert metrics.time_weighted_avg([]) is None
    assert metrics.time_weighted_avg([(0, Decimal("5"))]) == Decimal("5")


def lc(trig_t=None, action=None, end_reason=None):
    return {"triggered": None if trig_t is None else
            {"t": trig_t, "price": "1", "candle_t": trig_t},
            "post_trigger_action": action, "end_reason": end_reason}


def test_invalidation_eligibility_and_compliance():
    out = metrics.invalidation_outcomes([
        lc(100, "closed"),                 # eligible + compliant
        lc(100, "reversed"),               # compliant
        lc(100, None, "stop_loss"),        # exit before next round: compliant
        lc(100, None, "liquidation"),      # compliant
        lc(100, "reduced"),                # partial: NOT compliant
        lc(100, "held"),                   # not compliant
        lc(100, "increased"),              # not compliant
        lc(9999, "closed"),                # too late: not eligible
        lc(None),                          # never triggered: excluded entirely
    ], last_decision_opportunity_t=500)
    assert out["eligible"] == 7
    assert out["compliant"] == 4
    assert out["rate"] == round(4 / 7, 4)
    late = [d for d in out["detail"] if not d["eligible"]]
    assert len(late) == 1


def test_feature_reference_descriptive_only():
    r = metrics.feature_reference_frequency(
        ["RSI is 60 so I bought", "price action looks rangebound",
         "VWAP distance suggests nothing", "no features named"])
    assert r == 0.5
    assert "never" in metrics.feature_reference_frequency.__doc__.lower() or \
        "causal" in metrics.feature_reference_frequency.__doc__


def test_account_outcomes_and_invalidation_response_shapes():
    from engine import state
    a = state.new_account("BTC", "haiku", "raw")
    out = metrics.account_outcomes(a, Decimal("100"))
    assert out["equity"] == "10000.00" and out["terminal"] is False
    a["lifecycles"] = [dict(lc(50, "held"), lifecycle_id="x-L1", start_t=0,
                            invalidation={}, records=[])]
    resp = metrics.invalidation_response({"x": a}, {})
    assert resp[0]["post_trigger_action"] == "held"
