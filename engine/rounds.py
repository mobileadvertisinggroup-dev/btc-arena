"""Pair-atomic round coordination with deterministic wave rotation.

The model caller is INJECTED (offline stub in tests; live caller not wired here).
Caller protocol: caller(account_id, system, user, retry_message_or_None) ->
dict decision  |  raises TransportError.
"""
import hashlib
import json

from . import state, prompts, decisions, execution, replay as replay_mod

PAIR_COMMITTED = "PAIR_COMMITTED"
PAIR_ABORTED = "PAIR_ABORTED"
PAIR_TERMINAL_SPLIT = "PAIR_TERMINAL_SPLIT"
DEADLINE_S = 12 * 60


class TransportError(Exception):
    pass


def wave_order(round_seed):
    """Deterministic rotation of the 9 pairs; 3 pairs (6 requests) per wave."""
    pairs = [(c, m) for c in state.COINS for m in state.MODELS]
    off = int(hashlib.sha256(round_seed.encode()).hexdigest(), 16) % len(pairs)
    rotated = pairs[off:] + pairs[:off]
    return [rotated[i:i + 3] for i in range(0, len(rotated), 3)]


def _attempt(caller, acct, system, user, retry_msg, n, archive, ctx):
    rec = {"account_id": acct["id"], "pair_id": state.pair_id(acct["coin"], acct["model"]),
           "round_id": ctx["round_id"], "attempt_number": n,
           "generated_prompt_hash": hashlib.sha256(user.encode()).hexdigest(),
           "request_hash": hashlib.sha256((system + user + (retry_msg or "")).encode()).hexdigest(),
           "requested_model": ctx["model_ids"][acct["model"]],
           "returned_model": None, "raw_response": None, "parsed_tool_input": None,
           "schema_result": None, "semantic_validation_result": None,
           "fixed_rejection_reasons": [], "transport_error_category": None,
           "latency_ms": 0, "token_usage": None, "became_executed_decision": False}
    try:
        dec = caller(acct["id"], system, user, retry_msg)
        rec.update(returned_model=ctx["model_ids"][acct["model"]],
                   raw_response=json.dumps(dec, default=str),
                   parsed_tool_input=dec, schema_result="valid")
        archive.append(rec)
        return dec, rec
    except TransportError as e:
        rec.update(transport_error_category=str(e) or "transport")
        archive.append(rec)
        return None, rec


def collect_one(caller, acct, snapshot, cfg, archive, ctx, pregen):
    """Collect one valid decision for one account: 3 transport attempts, then
    1 validation retry. Returns (decision|None, reason|None)."""
    system, user = pregen[acct["id"]]
    n = 0
    dec = None
    for _ in range(3):
        n += 1
        dec, rec = _attempt(caller, acct, system, user, None, n, archive, ctx)
        if dec is not None:
            break
    if dec is None:
        return None, "transport_failure"
    reasons = decisions.validate(acct, dec, snapshot["P_T"])
    if not reasons:
        rec["semantic_validation_result"] = "valid"
        rec["became_executed_decision"] = True
        return dec, None
    rec["semantic_validation_result"] = "invalid"
    rec["fixed_rejection_reasons"] = reasons
    n += 1
    dec2, rec2 = _attempt(caller, acct, system, user,
                          decisions.retry_message(reasons), n, archive, ctx)
    if dec2 is None:
        return None, "transport_failure_on_retry"
    reasons2 = decisions.validate(acct, dec2, snapshot["P_T"])
    if reasons2:
        rec2["semantic_validation_result"] = "invalid"
        rec2["fixed_rejection_reasons"] = reasons2
        return None, "validation_failure_after_retry"
    rec2["semantic_validation_result"] = "valid"
    rec2["became_executed_decision"] = True
    return dec2, None


def run_boundary(T, snapshots, accounts, caller, cfg, clock=None):
    """One full boundary: freeze -> pregen all 18 prompts -> waves -> pair
    resolution -> apply at P_T -> (post-T replay is run by the caller of this
    function per coin). Returns (ledger, attempt_archive, prompt_archive)."""
    ctx = {"model_ids": {k: v["model"] for k, v in cfg["models"].items()}}
    ledger, archive, prompt_archive = [], [], {}
    # pregenerate & archive ALL prompts before any request (fairness contract)
    pregen = {}
    for acct in accounts.values():
        if acct["terminal"]:
            continue
        snap = snapshots.get(acct["coin"])
        if snap is None:
            continue
        pregen[acct["id"]] = prompts.render(acct, snap, cfg)
        prompt_archive[acct["id"]] = pregen[acct["id"]][1]
    seed = f"v1-ALL-{T}"
    for wave in wave_order(seed):
        for coin, model in wave:
            snap = snapshots.get(coin)
            rid = prompts.round_id(coin, T)
            ctx["round_id"] = rid
            pid = state.pair_id(coin, model)
            a_raw = accounts[state.account_id(coin, model, "raw")]
            a_ta = accounts[state.account_id(coin, model, "ta")]
            if snap is None:
                ledger.append({"round_id": rid, "pair": pid, "status": PAIR_ABORTED,
                               "reason": "DATA_UNAVAILABLE"})
                continue
            if clock is not None and clock() > T + DEADLINE_S:
                ledger.append({"round_id": rid, "pair": pid, "status": PAIR_ABORTED,
                               "reason": "deadline_exceeded"})
                continue
            terminals = [a for a in (a_raw, a_ta) if a["terminal"]]
            live = [a for a in (a_raw, a_ta) if not a["terminal"]]
            if terminals:
                # PAIR_TERMINAL_SPLIT: survivor trades alone
                results = {}
                for a in live:
                    dec, why = collect_one(caller, a, snap, cfg, archive, ctx, pregen)
                    results[a["id"]] = (dec, why)
                status = PAIR_TERMINAL_SPLIT
                if all(d is not None for d, _ in results.values()):
                    for a in live:
                        _commit_account(a, results[a["id"]][0], snap, T)
                ledger.append({"round_id": rid, "pair": pid, "status": status,
                               "terminal": [a["id"] for a in terminals],
                               "reason": None})
                continue
            dec_raw, why_raw = collect_one(caller, a_raw, snap, cfg, archive, ctx, pregen)
            dec_ta, why_ta = collect_one(caller, a_ta, snap, cfg, archive, ctx, pregen)
            if dec_raw is not None and dec_ta is not None:
                _commit_account(a_raw, dec_raw, snap, T)
                _commit_account(a_ta, dec_ta, snap, T)
                ledger.append({"round_id": rid, "pair": pid, "status": PAIR_COMMITTED,
                               "reason": None})
            else:
                cause_arm = "raw" if dec_raw is None else "ta"
                ledger.append({"round_id": rid, "pair": pid, "status": PAIR_ABORTED,
                               "reason": (why_raw or why_ta),
                               "caused_by_arm": cause_arm})
    return ledger, archive, prompt_archive


def _commit_account(acct, dec, snap, T):
    lc = acct["lifecycle"]
    pre_side = state.side(acct)
    pre_qty = abs(acct["qty"])
    pending_response = (lc is not None and lc["triggered"] is not None
                       and lc.get("post_trigger_action") is None)
    execution.apply_decision(acct, dec, snap["P_T"], T)
    acct["n_decisions"] += 1
    acct["theses"] = (acct["theses"] + [{"t": prompts._iso(T),
                                         "text": str(dec.get("thesis", ""))[:2000]}])[-3:]
    if pending_response:
        post_side = state.side(acct)
        post_qty = abs(acct["qty"])
        if post_side == "flat":
            act = "closed"
        elif post_side != pre_side:
            act = "reversed"
        elif post_qty < pre_qty:
            act = "reduced"
        elif post_qty > pre_qty:
            act = "increased"
        else:
            act = "held"
        lc["post_trigger_action"] = act


def post_boundary_replay(accounts, coin, candles_1m_after_T, records):
    """One common post-T replay per coin (virtual event time, ruling V1.2 G)."""
    coin_accounts = [a for a in accounts.values() if a["coin"] == coin]
    return replay_mod.replay(coin_accounts, candles_1m_after_T, records)
