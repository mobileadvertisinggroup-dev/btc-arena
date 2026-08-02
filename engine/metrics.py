"""Behavioural and performance metrics with the locked V1.2 denominators."""
from decimal import Decimal

from . import state

FEATURE_TERMS = ("RSI", "SMA", "ATR", "volume ratio", "VWAP")


def account_outcomes(acct, mark):
    eq = state.equity_at(acct, mark)
    realized = acct["E"] - state.START_CASH
    return {
        "id": acct["id"], "coin": acct["coin"], "model": acct["model"],
        "arm": acct["arm"],
        "equity": str(eq),
        "realized_pnl": str(realized),
        "unrealized_pnl": str(eq - acct["E"]),
        "total_return_pct": str((eq / state.START_CASH - 1) * 100),
        "fees": str(acct["fees_total"]),
        "n_trades": len(acct["trades"]),
        "n_decisions": acct["n_decisions"],
        "terminal": acct["terminal"],
    }


def reliability(attempts, ledger):
    firsts = [a for a in attempts if a["attempt_number"] == 1]
    valid_first = [a for a in firsts if a.get("semantic_validation_result") == "valid"]
    retries = [a for a in attempts if a.get("fixed_rejection_reasons")
               and a.get("semantic_validation_result") == "invalid"]
    corrected = [a for a in attempts if a["attempt_number"] > 1
                 and a.get("became_executed_decision")]
    transport = [a for a in attempts if a.get("transport_error_category")]
    aborts = [e for e in ledger if e["status"] == "PAIR_ABORTED"]
    return {
        "first_attempt_validity_rate": _rate(len(valid_first), len(firsts)),
        "validation_retry_rate": _rate(len(retries), len(firsts)),
        "successful_correction_rate": _rate(len(corrected), len(retries)),
        "transport_retry_count": len(transport),
        "pair_abort_rate": _rate(len(aborts), len(ledger)),
        "aborts_caused_by_arm": {arm: sum(1 for e in aborts
                                          if e.get("caused_by_arm") == arm)
                                 for arm in ("raw", "ta")},
    }


def _rate(n, d):
    return None if d == 0 else round(n / d, 4)


def paired_behaviour(decision_log):
    """decision_log rows: {round_id, coin, model, arm, position, size_usd,
    pre_equity, stop, tp} — only PAIR_COMMITTED rounds enter (caller filters)."""
    pairs = {}
    for row in decision_log:
        pairs.setdefault((row["round_id"], row["model"]), {})[row["arm"]] = row
    both = [p for p in pairs.values() if "raw" in p and "ta" in p]
    executable = [p for p in both
                  if p["raw"]["position"] in ("long", "short", "flat")
                  and p["ta"]["position"] in ("long", "short", "flat")]
    disagree = [p for p in executable
                if p["raw"]["position"] != p["ta"]["position"]]
    conflicts = [p for p in executable
                 if {p["raw"]["position"], p["ta"]["position"]} == {"long", "short"}]
    size_diffs = []
    for p in executable:
        mults = []
        for arm in ("raw", "ta"):
            eqv = Decimal(str(p[arm]["pre_equity"]))
            mults.append(Decimal(str(p[arm]["size_usd"] or 0)) / eqv if eqv else Decimal(0))
        size_diffs.append(abs(mults[0] - mults[1]))
    positioned = lambda arm: [p for p in executable if p[arm]["position"] != "flat"]
    stop_usage = {arm: _rate(sum(1 for p in positioned(arm) if p[arm].get("stop") is not None),
                             len(positioned(arm))) for arm in ("raw", "ta")}
    tp_usage = {arm: _rate(sum(1 for p in positioned(arm) if p[arm].get("tp") is not None),
                           len(positioned(arm))) for arm in ("raw", "ta")}
    return {
        "n_paired_rounds": len(executable),
        "direction_disagreement_rate": _rate(len(disagree), len(executable)),
        "direction_conflicts": len(conflicts),
        "mean_abs_size_diff_equity_multiples":
            str(sum(size_diffs, Decimal(0)) / len(size_diffs)) if size_diffs else None,
        "stop_usage": stop_usage, "tp_usage": tp_usage,
    }


def feature_reference_frequency(theses):
    """Descriptive only: thesis explicitly names a supplied feature. Never causal."""
    hits = [t for t in theses if any(term.lower() in t.lower() for term in FEATURE_TERMS)]
    return _rate(len(hits), len(theses))


def invalidation_response(accounts, ledger_by_pair):
    out = []
    for acct in accounts.values():
        lc = acct.get("lifecycle")
        for lifecycle_rec in ([lc] if lc else []):
            if lifecycle_rec["triggered"] is not None:
                out.append({"id": acct["id"],
                            "triggered_t": lifecycle_rec["triggered"]["t"],
                            "post_trigger_action": lifecycle_rec.get("post_trigger_action"),
                            "records": lifecycle_rec["records"]})
    return out


def committed_only(ledger):
    """Paired-behaviour denominators use PAIR_COMMITTED rounds only; aborted,
    skipped, pre-launch, and post-terminal rounds are excluded and counted
    separately (Ruling 005.10)."""
    return [e for e in ledger if e.get("status") == "PAIR_COMMITTED"]


def excluded_counts(ledger):
    out = {}
    for e in ledger:
        s = e.get("status")
        if s and s != "PAIR_COMMITTED":
            out[s] = out.get(s, 0) + 1
    return out


def time_weighted_avg(series):
    """series: [(t, Decimal equity)] sorted; TWA over the covered span."""
    if len(series) < 2:
        return series[0][1] if series else None
    num = Decimal(0)
    for (t0, e0), (t1, _) in zip(series, series[1:]):
        num += e0 * (t1 - t0)
    return num / (series[-1][0] - series[0][0])


def turnover(total_executed_notional, twae):
    if not twae or twae == 0:
        return None
    return total_executed_notional / twae


def invalidation_outcomes(lifecycles, last_decision_opportunity_t):
    """Eligibility: triggered early enough for >=1 later committed decision
    opportunity. Compliance (binary): no longer exposed in the invalidated
    direction by the next committed pair-round — full close, reversal, stop,
    or liquidation qualifies; partial reduction NEVER counts."""
    eligible, compliant, detail = 0, 0, []
    for lc in lifecycles:
        if lc.get("triggered") is None:
            continue
        elig = lc["triggered"]["t"] <= last_decision_opportunity_t
        act = lc.get("post_trigger_action")
        exit_reason = lc.get("end_reason")
        ok = (act in ("closed", "reversed")
              or exit_reason in ("stop_loss", "liquidation",
                                 "decision_close", "decision_flip"))
        if act in ("reduced", "held", "increased") and \
                exit_reason not in ("stop_loss", "liquidation"):
            ok = False
        if elig:
            eligible += 1
            if ok:
                compliant += 1
        detail.append({"eligible": elig, "compliant": ok, "action": act,
                       "end_reason": exit_reason})
    return {"eligible": eligible, "compliant": compliant,
            "rate": _rate(compliant, eligible), "detail": detail}
