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
import copy
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
                     abort_all_reason=None, resolution_deadline=None,
                     replay_deadline=None, pre_replay_spec=None,
                     hard_deadline=None):
    """Returns (ledger_entries, attempt_records, archived_user_prompts).

    `deadline` (Ruling 012.3): the authoritative ABSOLUTE collection deadline
    in `clock`'s domain. Production always passes T + collection_deadline_
    seconds anchored at the SCHEDULED boundary, so publication, market
    retrieval, grace waits, and restarts all consume the same T..T+12min
    budget — entering this coordinator late never grants new time, and a
    restart never resets the deadline. When omitted (offline tooling/tests),
    the legacy anchor `clock() + hard_terminal_deadline_seconds_after_T`
    applies.

    `abort_all_reason` (official-run Mentor Ruling 2): when set, the caller
    is NEVER invoked — every non-finalized pair aborts with exactly this
    reason and the prompt archive records not_called markers. Boundary marks
    still freeze and the boundary still becomes terminal.

    `resolution_deadline` / `replay_deadline` (Mentor Ruling 014.5, official
    runs pass T+630 for both): a live/terminal-split pair whose resolution
    has not begun by resolution_deadline aborts with deadline_exceeded
    instead of committing; replay stops processing candles at replay_deadline
    and latches CATCHUP_REQUIRED with the watermark preserved — the standard
    catch-up path resumes it next boundary. Both default to None (legacy
    behavior, used by all pilot-era tests).

    `pre_replay_spec` (Mentor Ruling 016.1 — LIVE EVENT ORDER): 1m candles
    STRICTLY BEFORE T, replayed against the pre-T account state BEFORE marks
    freeze, prompts render, or any model is called, with the phase persisted
    ("pre_replay" -> "decision") so a crash resumes idempotently and never
    replays a candle twice. Stops/TPs/invalidations/liquidations from the
    prior hour are therefore visible in the T prompts, and a candle with
    timestamp < T can never touch an action taken at T. If a coin's
    pre-decision replay cannot complete, the standard CATCHUP_REQUIRED /
    COIN_TERMINATED policy blocks that coin's pairs with ZERO model calls.
    The official runner uses ONLY pre_replay_spec; the legacy `replay_spec`
    (post-decision, post-T candles) remains for offline tooling and the
    frozen pilot-era tests.

    `hard_deadline` (Mentor Ruling 016.3): absolute T+720 bound for the
    boundary-completion checkpoint — a completion persisting at/after it is
    still terminal but NEVER silent (late_termination_at + ledger event)."""
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
                "phase": ("pre_replay" if pre_replay_spec else "decision"),
                "replay_watermark": meta.get("replay_watermark", {}),
                "replay_next_required": meta.get("replay_next_required", {}),
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

    def _replay_pass(spec_map, crash_label):
        """Exact-interval 1m replay: 10h catch-up policy (008.6),
        transactional per-candle durability (015.3/016.3) — replay runs on
        deep copies, the authoritative clock is read after the candle's work
        AND immediately before the atomic state replacement; a late candle
        is discarded whole with the watermark preserved."""
        for coin, spec in (spec_map or {}).items():
            if meta["coin_terminated"].get(coin):
                continue
            # EXACT REQUIRED-MINUTE TRACKING (Mentor Ruling 017.2): every
            # coin carries a persisted replay_next_required pointer —
            # maintained even when all accounts are flat — and replay ALWAYS
            # resumes exactly there. Supplied data that starts later than the
            # required minute is refused as a gap; the pointer NEVER advances
            # to a newer spec.start, so a missing hour can never be silently
            # skipped and REPLAY_COMPLETE can never be reached over a gap.
            req_map = meta.setdefault("replay_next_required", {})
            required = req_map.get(coin)
            if required is None:
                required = spec["start"]
                req_map[coin] = required
            eff_start = required
            end = spec["end"]
            if eff_start >= end:
                continue
            ids = [a["id"] for a in accounts.values() if a["coin"] == coin]

            def _apply_candles(candle_list, append=True):
                """Returns the first UNAPPLIED candle t (deadline
                truncation/discard) or None when all applied durably."""
                for c in candle_list:
                    if replay_deadline is not None \
                            and clock() >= replay_deadline:
                        return c["t"]
                    # STRICT VALUE VALIDATION (Ruling 019.3): a malformed
                    # candle never executes anything — it becomes the gap
                    try:
                        marketdata.validate_candle_values(c)
                    except marketdata.DataUnavailable:
                        return c["t"]
                    recs = []
                    originals = {a2: accounts[a2] for a2 in ids}
                    prepared = {a2: copy.deepcopy(accounts[a2])
                                for a2 in ids}
                    replay_mod.replay([prepared[a2] for a2 in ids], [c],
                                      recs)
                    if replay_deadline is not None \
                            and clock() >= replay_deadline:
                        return c["t"]                # discard prepared
                    cand_meta = copy.deepcopy(meta)
                    cand_meta["replay_watermark"][coin] = c["t"]
                    cand_meta["replay_next_required"][coin] = c["t"] + 60
                    for i, rc in enumerate(recs):
                        persistence.outbox_add(
                            cand_meta, f"{coin}:{c['t']}:{i}:{rc['e']}",
                            {"round_id": prompts.round_id(coin, T),
                             "replay": [rc]})
                    for a2 in ids:
                        accounts[a2] = prepared[a2]
                    if not persistence.save_state_tx(spath, accounts,
                                                     cand_meta, clock,
                                                     replay_deadline):
                        for a2 in ids:               # nothing durable changed
                            accounts[a2] = originals[a2]
                        return c["t"]
                    meta.clear()
                    meta.update(cand_meta)
                    persistence.flush_outbox(store, accounts, meta)
                    if recs and append:
                        ledger.append({"round_id": prompts.round_id(coin, T),
                                       "replay": recs})
                    _crash(crash_at, crash_label)
                return None

            def _latch_catchup(t_gap):
                meta["replay_state"][coin] = {
                    "status": "CATCHUP_REQUIRED", "gap_since": t_gap,
                    "detail": "replay_deadline_reached"}
                ev = {"round_id": prompts.round_id(coin, T),
                      "replay": [{"e": "CATCHUP_REQUIRED",
                                  "gap_since": t_gap}]}
                persistence.outbox_add(
                    meta, f"{coin}:{t_gap}:CATCHUP_REQUIRED", ev)
                persistence.save_state(spath, accounts, meta)
                persistence.flush_outbox(store, accounts, meta)
                ledger.append(ev)
            try:
                candles = marketdata.validate_1m_coverage(spec["candles"],
                                                          eff_start, end)
            except marketdata.DataUnavailable as e:
                # FIRST-ANOMALY SCAN over the ORIGINAL ORDERED series
                # (Rulings 008.6 + 020.1): ambiguous input is NEVER collapsed
                # into a timestamp map. A duplicate timestamp creates a gap
                # AT that minute — neither conflicting copy may execute;
                # out-of-order and misaligned data follow the same
                # no-execution rule; only the clean contiguous prefix
                # STRICTLY BEFORE the first anomaly replays, preserving the
                # last trustworthy watermark.
                in_range = [c for c in spec["candles"]
                            if eff_start <= c["t"] < end]
                anomaly = None
                want = eff_start
                seen = set()
                accepted = []
                for i, c in enumerate(in_range):
                    if c["t"] in seen:               # duplicate ANYWHERE
                        anomaly = c["t"]
                        break
                    if c["t"] != want:               # gap/misorder/misalign
                        anomaly = min(c["t"], want)
                        break
                    try:
                        marketdata.validate_candle_values(c)
                    except marketdata.DataUnavailable:
                        anomaly = c["t"]
                        break
                    seen.add(c["t"])
                    accepted.append(c)
                    want += 60
                prefix = ([c for c in accepted if c["t"] < anomaly]
                          if anomaly is not None else accepted)
                truncated = _apply_candles(prefix, append=False) \
                    if prefix else None
                if truncated is not None:
                    eff_start = truncated
                elif anomaly is not None:
                    eff_start = anomaly
                else:
                    eff_start = want
                unresolved = end - eff_start
                if unresolved > MAX_GAP_S:
                    meta["coin_terminated"][coin] = True
                    meta["replay_state"][coin] = {
                        "status": "COIN_TERMINATED", "gap_since": eff_start}
                    ev = {"round_id": prompts.round_id(coin, T),
                          "replay": [{"e": "COIN_TERMINATED",
                                      "reason": "replay_integrity_loss",
                                      "unresolved_s": unresolved}]}
                else:
                    meta["replay_state"][coin] = {
                        "status": "CATCHUP_REQUIRED", "gap_since": eff_start,
                        "detail": str(e)[:200]}
                    ev = {"round_id": prompts.round_id(coin, T),
                          "replay": [{"e": "CATCHUP_REQUIRED",
                                      "gap_since": eff_start}]}
                persistence.outbox_add(
                    meta, f"{coin}:{eff_start}:{ev['replay'][0]['e']}", ev)
                persistence.save_state(spath, accounts, meta)
                persistence.flush_outbox(store, accounts, meta)
                ledger.append(ev)
                continue
            meta["replay_state"][coin] = {"status": "REPLAY_COMPLETE"}
            truncated = _apply_candles(candles)
            if truncated is not None:
                _latch_catchup(truncated)

    # 2b. PRE-DECISION REPLAY (Ruling 016.1): candles strictly < T applied
    # to the pre-T account state BEFORE marks are re-frozen for prompts and
    # BEFORE any prompt/model work. Phase-persisted: a crash here resumes
    # idempotently (watermark) and prompt generation only ever sees the
    # post-replay state.
    if pre_replay_spec:
        for coin, spec in pre_replay_spec.items():
            # only [start, end) is ever replayed; end must not reach past T
            if spec["end"] > T:
                raise ValueError(
                    f"pre_replay_spec for {coin} ends at {spec['end']} > "
                    f"T={T} — pre-decision replay must be strictly pre-T")
    def _adopt_1m_marks():
        # AUTHORITATIVE 1m MARK (Mentor Rulings 019.1 + 021): when the
        # prompt snapshot is unavailable but the coin's 1m stream is
        # COMPLETE through T (replay_next_required == T), the SHARED
        # authoritative-mark rule (marketdata.authoritative_1m_mark —
        # EXACTLY ONE strictly validated T-60 candle) supplies the boundary
        # mark, so marked equity, equity history and dashboard P&L never go
        # null merely because 1h/1d prompt data failed. Zero OR MULTIPLE
        # T-60 candles (ambiguous duplicate) => no mark, no arbitrary
        # selection; a true 1m failure fabricates nothing (the mark stays
        # None and CATCHUP applies).
        for coin, spec in (pre_replay_spec or {}).items():
            if meta.get("marks", {}).get(coin) is not None:
                continue
            if meta.get("replay_next_required", {}).get(coin) != T:
                continue                 # 1m stream not proven complete
            try:
                mark = marketdata.authoritative_1m_mark(spec["candles"], T)
            except marketdata.DataUnavailable:
                continue                 # ambiguous/absent/malformed: no mark
            meta["marks"][coin] = str(mark)

    if pre_replay_spec and meta.get("phase") == "pre_replay":
        _replay_pass(pre_replay_spec, "during_pre_replay")
        _adopt_1m_marks()
        meta["phase"] = "decision"
        persistence.save_state(spath, accounts, meta)

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
        deadline = clock() + cfg["collection"][
            "hard_terminal_deadline_seconds_after_T"]
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
            # RESOLUTION DEADLINE (Ruling 014.5): a pair whose resolution has
            # not begun by the bound never commits — it aborts honestly. Late
            # trades can therefore never execute past T+10:30.
            if resolution_deadline is not None \
                    and kind in ("live", "terminal_split") \
                    and clock() >= resolution_deadline:
                kind, reason, terminal_ids = "abort", "deadline_exceeded", None
            a_raw = accounts[state.account_id(coin, model, "raw")]
            a_ta = accounts[state.account_id(coin, model, "ta")]
            snap = snapshots.get(coin)
            exec_links = []
            swapped_originals = {}
            # TRANSACTIONAL RESOLUTION (Ruling 015.3): all validation and
            # accounting run against DEEP COPIES; the authoritative clock is
            # read AFTER every piece of resolution work is prepared and only
            # a fully prepared PRE-DEADLINE result is swapped in and made
            # durable. A late preparation is discarded whole: zero account
            # mutation, zero trades, PAIR_ABORTED/deadline_exceeded.
            def _too_late():
                return (resolution_deadline is not None
                        and clock() >= resolution_deadline)

            def _late_abort():
                return {"round_id": rid, "pair": pid,
                        "status": rounds.PAIR_ABORTED,
                        "reason": "deadline_exceeded"}
            if kind == "abort":
                entry = {"round_id": rid, "pair": pid,
                         "status": rounds.PAIR_ABORTED, "reason": reason}
            elif kind == "terminal_split":
                live = [a for a in (a_raw, a_ta) if not a["terminal"]]
                outs = [results.get(a["id"], (None, "missing", None)) for a in live]
                entry = {"round_id": rid, "pair": pid,
                         "status": rounds.PAIR_TERMINAL_SPLIT,
                         "terminal": terminal_ids, "reason": None}
                if all(o[0] is not None for o in outs):
                    _crash(crash_at, "after_validate")
                    prepared = [copy.deepcopy(a) for a in live]
                    for p, o in zip(prepared, outs):
                        rounds._commit_account(p, o[0], snap, T)
                    if _too_late():
                        entry = _late_abort()            # discard prepared
                    else:
                        for a, p, o in zip(live, prepared, outs):
                            swapped_originals[p["id"]] = a
                            accounts[p["id"]] = p
                            exec_links.append(o[2])
                        _crash(crash_at, "after_execute")
            else:
                o_raw = results.get(a_raw["id"], (None, "missing", None))
                o_ta = results.get(a_ta["id"], (None, "missing", None))
                if o_raw[0] is not None and o_ta[0] is not None:
                    _crash(crash_at, "after_validate")
                    prep_raw = copy.deepcopy(a_raw)
                    prep_ta = copy.deepcopy(a_ta)
                    rounds._commit_account(prep_raw, o_raw[0], snap, T)
                    rounds._commit_account(prep_ta, o_ta[0], snap, T)
                    if _too_late():
                        entry = _late_abort()            # discard prepared
                    else:
                        swapped_originals = {a_raw["id"]: a_raw,
                                             a_ta["id"]: a_ta}
                        accounts[a_raw["id"]] = prep_raw
                        accounts[a_ta["id"]] = prep_ta
                        exec_links = [o_raw[2], o_ta[2]]
                        _crash(crash_at, "after_execute")
                        entry = {"round_id": rid, "pair": pid,
                                 "status": rounds.PAIR_COMMITTED,
                                 "reason": None}
                else:
                    entry = {"round_id": rid, "pair": pid,
                             "status": rounds.PAIR_ABORTED,
                             "reason": o_raw[1] or o_ta[1],
                             "caused_by_arm": "raw" if o_raw[0] is None else "ta"}
            # FINALIZE (Rulings 008 + 016.3): one atomic checkpoint. For a
            # TRADE-BEARING result the FULL serialized state is prepared and
            # fsynced FIRST and the authoritative clock is read immediately
            # before the atomic replacement — a late prepared commit is
            # discarded whole (in-memory swaps reverted, no account, fee,
            # trade, lifecycle, link or watermark survives) and the pair is
            # re-finalized as PAIR_ABORTED/deadline_exceeded.
            trade_bearing = bool(exec_links)
            cand_meta = copy.deepcopy(meta)
            persistence.outbox_add(cand_meta, f"{rid}:{pid}:{entry['status']}",
                                   entry)
            for link in exec_links:
                persistence.outbox_add(cand_meta, f"{rid}:{pid}:exec:{link}",
                                       {"e": "executed_attempt",
                                        "round_id": rid, "pair": pid,
                                        "attempt_id": link})
            cand_meta["finalized_pairs"][pid] = entry["status"]
            durable = persistence.save_state_tx(
                spath, accounts, cand_meta, clock,
                resolution_deadline if trade_bearing else None)
            if not durable:
                for aid2, orig in swapped_originals.items():
                    accounts[aid2] = orig            # nothing survives
                entry = _late_abort()
                exec_links = []
                cand_meta = copy.deepcopy(meta)
                persistence.outbox_add(
                    cand_meta, f"{rid}:{pid}:{entry['status']}", entry)
                cand_meta["finalized_pairs"][pid] = entry["status"]
                persistence.save_state(spath, accounts, cand_meta)
            meta.clear()
            meta.update(cand_meta)
            for link in exec_links:
                for rec in all_attempts:
                    if rec["attempt_id"] == link:
                        rec["became_executed_decision"] = True   # in-memory only
            _crash(crash_at, "after_checkpoint")
            persistence.flush_outbox(store, accounts, meta,
                                     crash=lambda p: _crash(crash_at, p))
            ledger.append(entry)
            _crash(crash_at, "after_finalize")
            if not done_one:
                done_one = True
                _crash(crash_at, "after_one_pair")

    # 6. post-decision replay (LEGACY/offline path only — the official
    # runner uses pre_replay_spec per Ruling 016.1)
    _replay_pass(replay_spec, "during_replay")

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
    # BOUNDARY-COMPLETION persistence (Ruling 016.3): terminal, and never
    # silently late — completion at/after the hard T+720 bound is recorded
    # explicitly in state and the ledger before it becomes durable.
    if not persistence.save_state_tx(spath, accounts, meta, clock,
                                     hard_deadline):
        meta["late_termination_at"] = clock()
        ev = {"round_id": f"v1-ALL-{T}",
              "e": "LATE_TERMINATION",
              "hard_deadline": hard_deadline, "at": meta["late_termination_at"]}
        persistence.outbox_add(meta, f"{T}:LATE_TERMINATION", ev)
        persistence.save_state(spath, accounts, meta)
        persistence.flush_outbox(store, accounts, meta)
        ledger.append(ev)
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
    """Mark the persisted boundary as recovering (rule: abort non-finalized).

    Ruling 016.1 refinement: a crash DURING the pre-decision replay phase —
    before any prompt was archived, hence provably zero model calls — is NOT
    marked recovering: the restart resumes the same boundary normally (the
    replay watermark guarantees no candle replays twice). Once any prompt is
    archived the frozen predeclared rule applies unchanged."""
    approved = config_mod.load_launch_manifest(store)
    config_mod.verify_integrity(approved)
    spath = os.path.join(store, "state.json")
    accounts, meta = persistence.load_state(spath, expect_full_roster=True)
    if not meta.get("boundary_complete"):
        in_pre_replay = (meta.get("phase") == "pre_replay"
                         and not persistence.read_prompt_archive(
                             store, _seed(meta.get("boundary"))))
        if not in_pre_replay:
            meta["_recovering"] = True
            persistence.save_state(spath, accounts, meta)
    return meta
