"""Checkpointed boundary runner with crash recovery.

State machine (persisted in state.json meta, updated only by atomic writes):
  boundary start   -> meta {boundary: T, finalized_pairs: {}, replay_watermark: {},
                            boundary_complete: False}
  per pair         -> collect -> validate -> execute (in-memory)
                      -> FINALIZE = one atomic save_state persisting the pair's
                         executed accounts + meta.finalized_pairs[pair]=status
  post-T replay    -> per candle: apply in-memory, then atomic save_state with
                      meta.replay_watermark[coin]=candle_t (redo-safe)
  boundary end     -> meta.boundary_complete = True

PREDECLARED DETERMINISTIC RECOVERY RULE: on restart of the same boundary, any
pair not FINALIZED in the persisted meta is recorded PAIR_ABORTED with reason
"crash_recovery" — it is never resumed. Finalized pairs stand untouched (their
effects are already inside the persisted state). Replay resumes strictly after
the persisted watermark, so no candle's effects can apply twice. Round/pair IDs
key everything, making reprocessing idempotent. Attempts/ledger files are
append-only archives deduplicated on read by (round_id, pair_id, attempt#).
"""
import os

from . import state, rounds, prompts, persistence, replay as replay_mod


class CrashError(Exception):
    pass


def _crash(crash_at, point):
    if crash_at == point:
        raise CrashError(point)


def run_checkpointed(T, snapshots, caller, cfg, store, crash_at=None,
                     candles_after_by_coin=None):
    """store: directory. Returns ledger entries written this invocation."""
    spath = os.path.join(store, "state.json")
    lpath = os.path.join(store, "ledger.jsonl")
    apath = os.path.join(store, "attempts.jsonl")
    accounts, meta = persistence.load_state(spath)
    if meta.get("boundary") != T:
        meta = {"boundary": T, "finalized_pairs": {}, "replay_watermark": {},
                "boundary_complete": False}
        persistence.save_state(spath, accounts, meta)
    ledger = []
    ctx = {"model_ids": {k: v["model"] for k, v in cfg["models"].items()}}

    # 1. pregenerate + archive all prompts (fairness contract)
    pregen = {}
    for acct in accounts.values():
        snap = snapshots.get(acct["coin"])
        if snap is None or acct["terminal"]:
            continue
        pregen[acct["id"]] = prompts.render(acct, snap, cfg)
    _crash(crash_at, "after_prompts")

    recovered = dict(meta["finalized_pairs"])
    done_one = False
    for wave in rounds.wave_order(f"v1-ALL-{T}"):
        for coin, model in wave:
            pid = state.pair_id(coin, model)
            rid = prompts.round_id(coin, T)
            ctx["round_id"] = rid
            if pid in recovered:
                continue                       # FINALIZED pairs stand
            if meta.get("_recovering"):
                entry = {"round_id": rid, "pair": pid, "status": rounds.PAIR_ABORTED,
                         "reason": "crash_recovery"}
                persistence.append_ledger(lpath, [entry])
                meta["finalized_pairs"][pid] = "PAIR_ABORTED"
                persistence.save_state(spath, accounts, meta)
                ledger.append(entry)
                continue
            snap = snapshots.get(coin)
            a_raw = accounts[state.account_id(coin, model, "raw")]
            a_ta = accounts[state.account_id(coin, model, "ta")]
            if snap is None:
                entry = {"round_id": rid, "pair": pid,
                         "status": rounds.PAIR_ABORTED, "reason": "DATA_UNAVAILABLE"}
            elif a_raw["terminal"] or a_ta["terminal"]:
                live = [a for a in (a_raw, a_ta) if not a["terminal"]]
                archive = []
                results = {a["id"]: rounds.collect_one(caller, a, snap, cfg,
                                                       archive, ctx, pregen)
                           for a in live}
                persistence.append_ledger(apath, archive)
                if all(d is not None for d, _ in results.values()):
                    for a in live:
                        rounds._commit_account(a, results[a["id"]][0], snap, T)
                entry = {"round_id": rid, "pair": pid,
                         "status": rounds.PAIR_TERMINAL_SPLIT,
                         "terminal": [a["id"] for a in (a_raw, a_ta) if a["terminal"]],
                         "reason": None}
            else:
                archive = []
                dec_raw, why_raw = rounds.collect_one(caller, a_raw, snap, cfg,
                                                      archive, ctx, pregen)
                persistence.append_ledger(apath, archive)
                archive = []
                _crash(crash_at, "after_first_attempt")   # attempt archived above
                dec_ta, why_ta = rounds.collect_one(caller, a_ta, snap, cfg,
                                                    archive, ctx, pregen)
                persistence.append_ledger(apath, archive)
                if dec_raw is not None and dec_ta is not None:
                    _crash(crash_at, "after_validate")
                    rounds._commit_account(a_raw, dec_raw, snap, T)
                    rounds._commit_account(a_ta, dec_ta, snap, T)
                    _crash(crash_at, "after_execute")
                    entry = {"round_id": rid, "pair": pid,
                             "status": rounds.PAIR_COMMITTED, "reason": None}
                else:
                    entry = {"round_id": rid, "pair": pid,
                             "status": rounds.PAIR_ABORTED,
                             "reason": why_raw or why_ta,
                             "caused_by_arm": "raw" if dec_raw is None else "ta"}
            # FINALIZE: one atomic persist of state + pair status + ledger line
            persistence.append_ledger(lpath, [entry])
            meta["finalized_pairs"][pid] = entry["status"]
            persistence.save_state(spath, accounts, meta)
            ledger.append(entry)
            _crash(crash_at, "after_finalize")
            if not done_one:
                done_one = True
                _crash(crash_at, "after_one_pair")

    # 2. one common post-T replay per coin, watermark-checkpointed per candle
    for coin, candles in (candles_after_by_coin or {}).items():
        wm = meta["replay_watermark"].get(coin, -1)
        coin_accounts = [a for a in accounts.values() if a["coin"] == coin]
        for c in candles:
            if c["t"] <= wm:
                continue                        # already applied and persisted
            recs = []
            replay_mod.replay(coin_accounts, [c], recs)
            meta["replay_watermark"][coin] = c["t"]
            persistence.save_state(spath, accounts, meta)
            if recs:
                persistence.append_ledger(lpath, [{"round_id": prompts.round_id(coin, T),
                                                   "replay": recs}])
            _crash(crash_at, "during_replay")
    meta["boundary_complete"] = True
    meta.pop("_recovering", None)
    persistence.save_state(spath, accounts, meta)
    return ledger


def recover(store):
    """Mark the persisted boundary as recovering (rule: abort non-finalized)."""
    spath = os.path.join(store, "state.json")
    accounts, meta = persistence.load_state(spath)
    if not meta.get("boundary_complete"):
        meta["_recovering"] = True
        persistence.save_state(spath, accounts, meta)
    return meta
