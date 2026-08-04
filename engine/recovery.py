"""AUTHORITATIVE PRODUCTION BOUNDARY COORDINATOR (checkpointed, crash-safe).

Architecture (Ruling 008):
  1. verify engine params against frozen config; verify state integrity.
  2. reconcile: flush any pending outbox events from a prior crash.
  3. render all eligible prompts and DURABLY archive them (fsync) before any
     model request; terminal / data-blocked accounts get not_called markers.
  4. collection: deterministic rotating waves of 3 matched pairs (6 requests)
     executed by a bounded ThreadPoolExecutor (config concurrency limit) under
     one hard MONOTONIC deadline for the whole stage; results completing at or
     after the deadline never execute.
  5. per-pair resolution in wave order: commit both twins at frozen P_T or
     abort the pair. FINALIZE = ONE atomic checkpoint (accounts + pair status
     + executed-attempt links + outbox events), then idempotent outbox flush
     to ledger.jsonl (publication), then mark-flushed checkpoint.
  6. replay: per coin, exact [replay_start, replay_end) 1m coverage is
     required; a gap preserves the watermark, sets CATCHUP_REQUIRED, and
     blocks that coin's future decisions until caught up; unresolved required
     history > 10h terminates the coin's arena (COIN_TERMINATED). Replay
     exits/latches/termination all publish through the same outbox.

PREDECLARED RECOVERY RULE: on restart of an incomplete boundary, any pair not
finalized in the checkpoint is PAIR_ABORTED("crash_recovery"); finalized pairs
stand; replay resumes strictly after the persisted watermark.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from . import (state, rounds, prompts, persistence, config as config_mod,
               marketdata, replay as replay_mod)

MAX_GAP_S = 10 * 3600


class CrashError(Exception):
    pass


class PromptArchiveError(Exception):
    """Prompt archival failed => zero model calls (integrity halt)."""


def _crash(crash_at, point):
    if crash_at == point:
        raise CrashError(point)


def _seed(T):
    return f"v1-ALL-{T}"


def run_checkpointed(T, snapshots, caller, cfg, store, crash_at=None,
                     replay_spec=None, clock=None, deadline=None,
                     abort_all_reason=None):
    """Returns (ledger_entries, attempt_records, archived_user_prompts).

    `deadline` (Ruling 012.3): the authoritative ABSOLUTE collection deadline
    in `clock`'s domain. Production always passes T + collection_deadline_
    seconds anchored at the SCHEDULED boundary, so publication, market
    retrieval, grace waits, and restarts all consume the same T..T+12min
    budget — entering this coordinator late never grants new time, and a
    restart never resets the deadline. When omitted (offline tooling/tests),
    the legacy anchor `clock() + collection_deadline_seconds` applies.

    `abort_all_reason` (official-run Mentor Ruling 2): when set, the caller
    is NEVER invoked — every non-finalized pair aborts with exactly this
    reason and the prompt archive records not_called markers. Boundary marks
    still freeze and the boundary still becomes terminal."""
    # RUNTIME INTEGRITY GATE (Ruling 009.1): load the immutable APPROVED
    # launch manifest (never rebuilt from the working tree) and verify every
    # engine/script/config/prompt/schema byte BEFORE touching state, prompts,
    # or any model caller. Mismatch => Integrity Halt A, zero model calls.
    approved = config_mod.load_launch_manifest(store)
    config_mod.verify_integrity(approved)
    state.verify_params(cfg)
    clock = clock or time.monotonic
    spath = os.path.join(store, "state.json")
    accounts, meta = persistence.load_state(spath, expect_full_roster=True)
    if meta.get("boundary") != T:
        # PERSISTED BOUNDARY MARKS (Ruling 011.2): freeze this boundary's
        # market marks (snapshot P_T per coin, or None when data-blocked) at
        # boundary start. Publication uses ONLY these persisted marks; a
        # crash-restart of the same boundary keeps the frozen values.
        meta = {"boundary": T, "finalized_pairs": {},
                "replay_watermark": meta.get("replay_watermark", {}),
                "boundary_complete": False,
                "coin_terminated": meta.get("coin_terminated", {}),
                "replay_state": meta.get("replay_state", {}),
                "outbox": meta.get("outbox", []),
                "flushed_ids": meta.get("flushed_ids", []),
                "marks": {c: (str(snapshots[c]["P_T"]) if snapshots.get(c)
                              else None)
                          for c in ("BTC", "ETH", "SOL")},
                "marks_T": T,
                "equity_history": meta.get("equity_history", {}),
                "equity_history_last": meta.get("equity_history_last")}
        persistence.save_state(spath, accounts, meta)
    meta.setdefault("coin_terminated", {})
    meta.setdefault("replay_state", {})
    ledger, all_attempts = [], []
    ctx = {"model_ids": {k: v["model"] for k, v in cfg["models"].items()},
           "transport_attempts_total":
               1 + cfg["request_payloads"]["common"]["transport_retries"]}
    # 2. reconcile any unflushed outbox from a prior crash (idempotent)
    persistence.flush_outbox(store, accounts, meta)

    # 3. durable prompt archive BEFORE any request (Ruling 008.3)
    archived = persistence.read_prompt_archive(store, _seed(T))
    entries, pregen = [], {}
    for acct in accounts.values():
        aid = acct["id"]
        blocked = (acct["terminal"] or meta["coin_terminated"].get(acct["coin"])
                   or snapshots.get(acct["coin"]) is None
                   or meta["replay_state"].get(acct["coin"], {}).get("status")
                   == "CATCHUP_REQUIRED")
        if aid in archived:
            e = archived[aid]
            if "user" in e:
                pregen[aid] = (e["system"], e["user"])   # bytes reused verbatim
            continue
        if abort_all_reason is not None:
            entries.append({"account_id": aid,
                            "pair_id": state.pair_id(acct["coin"], acct["model"]),
                            "round_id": prompts.round_id(acct["coin"], T),
                            "not_called": abort_all_reason})
            continue
        if blocked:
            entries.append({"account_id": aid,
                            "pair_id": state.pair_id(acct["coin"], acct["model"]),
                            "round_id": prompts.round_id(acct["coin"], T),
                            "not_called": ("terminal" if acct["terminal"]
                                           else "data_blocked")})
            continue
        system, user = prompts.render(acct, snapshots[acct["coin"]], cfg)
        pregen[aid] = (system, user)
        entries.append({"account_id": aid,
                        "pair_id": state.pair_id(acct["coin"], acct["model"]),
                        "round_id": prompts.round_id(acct["coin"], T),
                        "system": system, "user": user,
                        "prompt_hash": rounds.hashlib.sha256(user.encode()).hexdigest()})
    try:
        persistence.write_prompt_archive(store, _seed(T), entries)
    except Exception as e:
        raise PromptArchiveError(str(e))          # zero model calls
    _crash(crash_at, "after_prompts")

    # 4/5. wave collection + pair resolution under ONE hard deadline
    if deadline is None:
        deadline = clock() + cfg["collection"]["collection_deadline_seconds"]
    timeout_s = cfg["request_payloads"]["common"]["timeout_seconds"]
    concurrency = cfg["collection"]["concurrency_max_simultaneous_requests"]
    n_written = [0]
    valid_schema = _attempt_validator()

    def writer(rec):
        persistence.write_attempt(store, rec, validator=valid_schema)
        all_attempts.append(rec)
        n_written[0] += 1
        if n_written[0] == 1:
            _crash(crash_at, "after_first_attempt")

    def budget_ok():
        # a (re)try may not begin unless its full timeout budget fits (008.9)
        return clock() + timeout_s <= deadline

    recovered = dict(meta["finalized_pairs"])
    done_one = False
    for wave in rounds.wave_order(_seed(T)):
        live_tasks = {}
        pair_meta = {}
        for coin, model in wave:
            pid = state.pair_id(coin, model)
            rid = prompts.round_id(coin, T)
            if pid in recovered:
                continue
            a_raw = accounts[state.account_id(coin, model, "raw")]
            a_ta = accounts[state.account_id(coin, model, "ta")]
            if abort_all_reason is not None:
                pair_meta[pid] = ("abort", rid, abort_all_reason, None)
            elif meta.get("_recovering"):
                pair_meta[pid] = ("abort", rid, "crash_recovery", None)
            elif meta["coin_terminated"].get(coin):
                pair_meta[pid] = ("abort", rid, "COIN_TERMINATED", None)
            elif snapshots.get(coin) is None or \
                    meta["replay_state"].get(coin, {}).get("status") == "CATCHUP_REQUIRED":
                pair_meta[pid] = ("abort", rid, "DATA_UNAVAILABLE", None)
            elif clock() >= deadline:
                pair_meta[pid] = ("abort", rid, "deadline_exceeded", None)
            elif a_raw["terminal"] or a_ta["terminal"]:
                live = [a for a in (a_raw, a_ta) if not a["terminal"]]
                pair_meta[pid] = ("terminal_split", rid, None,
                                  [a["id"] for a in (a_raw, a_ta) if a["terminal"]])
                for a in live:
                    live_tasks[a["id"]] = (a, snapshots[coin], rid)
            else:
                pair_meta[pid] = ("live", rid, None, None)
                for a in (a_raw, a_ta):
                    live_tasks[a["id"]] = (a, snapshots[coin], rid)
        # bounded concurrent collection: both twins enter the same wave
        results = {}
        if live_tasks:
            def task(aid):
                a, snap, rid = live_tasks[aid]
                tctx = dict(ctx, round_id=rid)
                out = rounds.collect_one(caller, a, snap, cfg, tctx, pregen,
                                         writer, budget_ok)
                return aid, out, clock()
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = [ex.submit(task, aid) for aid in sorted(live_tasks)]
                for f in futs:
                    aid, out, done_at = f.result()
                    # results at/after the hard deadline never execute (008.9)
                    if done_at >= deadline:
                        out = (None, "deadline_exceeded", None)
                    results[aid] = out
        # deterministic per-pair resolution in wave order
        for coin, model in wave:
            pid = state.pair_id(coin, model)
            if pid in recovered or pid not in pair_meta:
                continue
            kind, rid, reason, terminal_ids = pair_meta[pid]
            a_raw = accounts[state.account_id(coin, model, "raw")]
            a_ta = accounts[state.account_id(coin, model, "ta")]
            snap = snapshots.get(coin)
            exec_links = []
            if kind == "abort":
                entry = {"round_id": rid, "pair": pid,
                         "status": rounds.PAIR_ABORTED, "reason": reason}
            elif kind == "terminal_split":
                live = [a for a in (a_raw, a_ta) if not a["terminal"]]
                outs = [results.get(a["id"], (None, "missing", None)) for a in live]
                if all(o[0] is not None for o in outs):
                    _crash(crash_at, "after_validate")
                    for a, o in zip(live, outs):
                        rounds._commit_account(a, o[0], snap, T)
                        exec_links.append(o[2])
                    _crash(crash_at, "after_execute")
                entry = {"round_id": rid, "pair": pid,
                         "status": rounds.PAIR_TERMINAL_SPLIT,
                         "terminal": terminal_ids, "reason": None}
            else:
                o_raw = results.get(a_raw["id"], (None, "missing", None))
                o_ta = results.get(a_ta["id"], (None, "missing", None))
                if o_raw[0] is not None and o_ta[0] is not None:
                    _crash(crash_at, "after_validate")
                    rounds._commit_account(a_raw, o_raw[0], snap, T)
                    rounds._commit_account(a_ta, o_ta[0], snap, T)
                    exec_links = [o_raw[2], o_ta[2]]
                    _crash(crash_at, "after_execute")
                    entry = {"round_id": rid, "pair": pid,
                             "status": rounds.PAIR_COMMITTED, "reason": None}
                else:
                    entry = {"round_id": rid, "pair": pid,
                             "status": rounds.PAIR_ABORTED,
                             "reason": o_raw[1] or o_ta[1],
                             "caused_by_arm": "raw" if o_raw[0] is None else "ta"}
            # FINALIZE: one atomic checkpoint (state + status + links + outbox)
            persistence.outbox_add(meta, f"{rid}:{pid}:{entry['status']}", entry)
            for link in exec_links:
                persistence.outbox_add(meta, f"{rid}:{pid}:exec:{link}",
                                       {"e": "executed_attempt", "round_id": rid,
                                        "pair": pid, "attempt_id": link})
                for rec in all_attempts:
                    if rec["attempt_id"] == link:
                        rec["became_executed_decision"] = True   # in-memory only
            meta["finalized_pairs"][pid] = entry["status"]
            persistence.save_state(spath, accounts, meta)
            _crash(crash_at, "after_checkpoint")
            persistence.flush_outbox(store, accounts, meta,
                                     crash=lambda p: _crash(crash_at, p))
            ledger.append(entry)
            _crash(crash_at, "after_finalize")
            if not done_one:
                done_one = True
                _crash(crash_at, "after_one_pair")

    # 6. replay with exact interval validation + 10h catch-up policy
    for coin, spec in (replay_spec or {}).items():
        if meta["coin_terminated"].get(coin):
            continue
        rs = meta["replay_state"].get(coin, {})
        wm = meta["replay_watermark"].get(coin)
        eff_start = (wm + 60) if wm is not None else spec["start"]
        eff_start = max(eff_start, rs.get("gap_since", eff_start))
        end = spec["end"]
        if eff_start >= end:
            continue
        try:
            candles = marketdata.validate_1m_coverage(spec["candles"],
                                                      eff_start, end)
        except marketdata.DataUnavailable as e:
            # process the contiguous prefix before the gap, preserving the
            # last trustworthy watermark; never process past the gap (008.6)
            prefix = []
            want = eff_start
            have = {c["t"]: c for c in spec["candles"]}
            while want in have:
                prefix.append(have[want])
                want += 60
            if prefix:
                coin_accounts = [a for a in accounts.values()
                                 if a["coin"] == coin]
                for c in prefix:
                    recs = []
                    replay_mod.replay(coin_accounts, [c], recs)
                    meta["replay_watermark"][coin] = c["t"]
                    for i, rc in enumerate(recs):
                        persistence.outbox_add(
                            meta, f"{coin}:{c['t']}:{i}:{rc['e']}",
                            {"round_id": prompts.round_id(coin, T),
                             "replay": [rc]})
                persistence.save_state(spath, accounts, meta)
                persistence.flush_outbox(store, accounts, meta)
                eff_start = want
            unresolved = end - eff_start
            if unresolved > MAX_GAP_S:
                meta["coin_terminated"][coin] = True
                meta["replay_state"][coin] = {"status": "COIN_TERMINATED",
                                              "gap_since": eff_start}
                ev = {"round_id": prompts.round_id(coin, T),
                      "replay": [{"e": "COIN_TERMINATED",
                                  "reason": "replay_integrity_loss",
                                  "unresolved_s": unresolved}]}
            else:
                meta["replay_state"][coin] = {"status": "CATCHUP_REQUIRED",
                                              "gap_since": eff_start,
                                              "detail": str(e)[:200]}
                ev = {"round_id": prompts.round_id(coin, T),
                      "replay": [{"e": "CATCHUP_REQUIRED",
                                  "gap_since": eff_start}]}
            persistence.outbox_add(meta, f"{coin}:{eff_start}:{ev['replay'][0]['e']}", ev)
            persistence.save_state(spath, accounts, meta)
            persistence.flush_outbox(store, accounts, meta)
            ledger.append(ev)
            continue
        meta["replay_state"][coin] = {"status": "REPLAY_COMPLETE"}
        coin_accounts = [a for a in accounts.values() if a["coin"] == coin]
        for c in candles:
            recs = []
            replay_mod.replay(coin_accounts, [c], recs)
            meta["replay_watermark"][coin] = c["t"]
            for i, rc in enumerate(recs):
                persistence.outbox_add(meta, f"{coin}:{c['t']}:{i}:{rc['e']}",
                                       {"round_id": prompts.round_id(coin, T),
                                        "replay": [rc]})
            persistence.save_state(spath, accounts, meta)
            persistence.flush_outbox(store, accounts, meta)
            if recs:
                ledger.append({"round_id": prompts.round_id(coin, T),
                               "replay": recs})
            _crash(crash_at, "during_replay")
    # DURABLE EQUITY HISTORY (Ruling 011.2): one point per completed boundary,
    # equity computed with THIS boundary's persisted mark; explicit null when
    # an open position has no valid mark. Idempotent across same-T re-runs.
    if meta.get("equity_history_last") != T:
        hist = meta.setdefault("equity_history", {})
        for aid, acct in accounts.items():
            mark = (meta.get("marks") or {}).get(acct["coin"])
            if state.side(acct) == "flat":
                eq = str(acct["E"])
            elif mark is not None:
                eq = str(state.equity_at(acct, Decimal(mark)))
            else:
                eq = None                    # never fabricated from cash
            hist.setdefault(aid, []).append(
                {"T": T, "equity": eq, "fees": str(acct["fees_total"])})
        meta["equity_history_last"] = T
    meta["boundary_complete"] = True
    meta.pop("_recovering", None)
    persistence.save_state(spath, accounts, meta)
    return ledger, all_attempts, {aid: p[1] for aid, p in pregen.items()}


def _attempt_validator():
    import jsonschema
    defs = config_mod.load_json("schemas/v1/records.schema.json")["$defs"]
    v = jsonschema.Draft202012Validator(defs["attempt"])

    def check(rec):
        import json as _json
        v.validate(_json.loads(_json.dumps(rec, default=str)))
    return check


def recover(store):
    """Mark the persisted boundary as recovering (rule: abort non-finalized)."""
    approved = config_mod.load_launch_manifest(store)
    config_mod.verify_integrity(approved)
    spath = os.path.join(store, "state.json")
    accounts, meta = persistence.load_state(spath, expect_full_roster=True)
    if not meta.get("boundary_complete"):
        meta["_recovering"] = True
        persistence.save_state(spath, accounts, meta)
    return meta
