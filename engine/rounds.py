"""Pure pair-round helpers: wave rotation, attempt collection, commit records.

The AUTHORITATIVE coordinator is engine/recovery.py. The model caller is
INJECTED: caller(account_id, system, user, retry_message_or_None) -> decision
(any JSON value) | raises TransportError.
"""
import hashlib
import json

from . import state, prompts, decisions, execution, replay as replay_mod

PAIR_COMMITTED = "PAIR_COMMITTED"
PAIR_ABORTED = "PAIR_ABORTED"
PAIR_TERMINAL_SPLIT = "PAIR_TERMINAL_SPLIT"


class TransportError(Exception):
    pass


def wave_order(round_seed):
    """Deterministic rotation of the 9 pairs; 3 pairs (6 requests) per wave."""
    pairs = [(c, m) for c in state.COINS for m in state.MODELS]
    off = int(hashlib.sha256(round_seed.encode()).hexdigest(), 16) % len(pairs)
    rotated = pairs[off:] + pairs[:off]
    return [rotated[i:i + 3] for i in range(0, len(rotated), 3)]


def attempt_id(round_id, account_id, n):
    return f"{round_id}:{account_id}:attempt{n}"


def _attempt(caller, acct, snapshot, system, user, retry_msg, n, ctx, writer):
    """One transport attempt: transport -> identity -> schema (Draft 2020-12)
    -> semantic. Handles ANY returned value without crashing. The durable
    record is written exactly once, immutable, with `became_executed_decision`
    ALWAYS false (Ruling 008.2) — execution is recorded later via immutable
    link events.

    Caller contract (Mentor Ruling 016.4): the PRODUCTION caller returns an
    audited response ENVELOPE — {"decision", "response_model", "response_id",
    "stop_reason", "latency_ms", "token_usage", "raw_response"} — whose
    metadata is archived verbatim. The actual response model id must EXACTLY
    equal the requested frozen model id; a mismatch is archived
    (identity_mismatch=true) and NEVER executes. Legacy/offline callers may
    return a bare decision: its unknown metadata is archived as null — never
    fabricated."""
    rec = {"account_id": acct["id"],
           "pair_id": state.pair_id(acct["coin"], acct["model"]),
           "round_id": ctx["round_id"], "attempt_number": n,
           "attempt_id": attempt_id(ctx["round_id"], acct["id"], n),
           "generated_prompt_hash": hashlib.sha256(user.encode()).hexdigest(),
           "request_hash": hashlib.sha256(
               (system + user + (retry_msg or "")).encode()).hexdigest(),
           "requested_model": ctx["model_ids"][acct["model"]],
           "returned_model": None, "raw_response": None,
           "parsed_tool_input": None, "schema_result": None,
           "semantic_validation_result": None, "fixed_rejection_reasons": [],
           "transport_error_category": None, "latency_ms": None,
           "token_usage": None, "became_executed_decision": False}
    try:
        ret = caller(acct["id"], system, user, retry_msg)
    except TransportError as e:
        rec["transport_error_category"] = str(e) or "transport"
        writer(rec)
        return None, "transport", rec
    if isinstance(ret, dict) and "decision" in ret and "response_model" in ret:
        dec = ret.get("decision")
        rec["returned_model"] = ret.get("response_model")
        rec["response_id"] = ret.get("response_id")
        rec["stop_reason"] = ret.get("stop_reason")
        rec["latency_ms"] = ret.get("latency_ms")
        rec["token_usage"] = ret.get("token_usage")
        rec["raw_response"] = (ret.get("raw_response")
                               or json.dumps(dec, default=str))
        if rec["returned_model"] != rec["requested_model"]:
            rec["identity_mismatch"] = True      # archived; never executes
            writer(rec)
            return None, "identity_mismatch", rec
    else:
        dec = ret                                # legacy/offline caller
        rec["raw_response"] = json.dumps(dec, default=str)
    schema_reasons = decisions.schema_validate(dec)
    if schema_reasons:
        rec["schema_result"] = "invalid"
        rec["fixed_rejection_reasons"] = schema_reasons
        writer(rec)
        return None, schema_reasons, rec
    rec["schema_result"] = "valid"
    rec["parsed_tool_input"] = dec
    sem = decisions.validate(acct, dec, snapshot["P_T"])
    rec["semantic_validation_result"] = "valid" if not sem else "invalid"
    rec["fixed_rejection_reasons"] = sem
    writer(rec)
    if sem:
        return None, sem, rec
    return dec, None, rec


def _conversation(caller, acct, snapshot, system, user, retry_msg, n0, ctx,
                  writer, budget_ok):
    """One conversation under the full transport policy: 1 initial attempt +
    cfg transport retries (= attempts_total, Ruling 008.7). A model-identity
    mismatch (Ruling 016.4) is retried like a transport anomaly — it can
    never execute. Returns (decision|None, why, last_n, last_rec)."""
    n = n0
    last_why = "transport"
    for _ in range(ctx["transport_attempts_total"]):
        if not budget_ok():
            return None, "deadline_exceeded", n, None
        n += 1
        dec, why, rec = _attempt(caller, acct, snapshot, system, user,
                                 retry_msg, n, ctx, writer)
        if why not in ("transport", "identity_mismatch"):
            return dec, why, n, rec
        last_why = why
    return None, ("identity_mismatch" if last_why == "identity_mismatch"
                  else "transport_failure"), n, None


def collect_one(caller, acct, snapshot, cfg, ctx, pregen, writer, budget_ok):
    """Schema-first collection with a single validation-retry conversation.
    Returns (decision|None, why|None, operative_attempt_id|None)."""
    system, user = pregen[acct["id"]]
    dec, why, n, rec = _conversation(caller, acct, snapshot, system, user,
                                     None, 0, ctx, writer, budget_ok)
    if dec is not None:
        return dec, None, rec["attempt_id"]
    if why in ("transport_failure", "deadline_exceeded", "identity_mismatch"):
        return None, why, None
    dec2, why2, n2, rec2 = _conversation(
        caller, acct, snapshot, system, user, decisions.retry_message(why),
        n, ctx, writer, budget_ok)
    if dec2 is not None:
        return dec2, None, rec2["attempt_id"]
    if why2 in ("transport_failure", "deadline_exceeded",
                "identity_mismatch"):
        return None, ("transport_failure_on_retry" if why2 == "transport_failure"
                      else why2), None
    return None, "validation_failure_after_retry", None


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
    """Pure helper: one common post-T replay per coin."""
    coin_accounts = [a for a in accounts.values() if a["coin"] == coin]
    return replay_mod.replay(coin_accounts, candles_1m_after_T, records)
