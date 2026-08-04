"""Ruling 008 regressions: schema probes, executed flags, prompt/attempt
durability, freshness, 10h policy, transport counts, concurrency, deadline,
outbox atomicity, manifest, config authority, lifecycles, watch, corruption."""
import json
import os
import shutil
import tempfile
import threading
from decimal import Decimal

import pytest

from conftest import (T0, ScriptedCaller, flat_decision, long_decision,
                      load_fix, run_prod)
from engine import (state, decisions, execution, replay, config, persistence,
                    recovery, rounds, marketdata)

P = Decimal("100.00")
HOUR, MIN = 3600, 60


def store_with(accounts, tmp=None):
    store = tmp or tempfile.mkdtemp(prefix="arena-r8-")
    persistence.save_state(store + "/state.json", accounts, {"boundary": None})
    config.write_launch_manifest(store)
    return store


# ---- 1. schema validation probes (previously undetected) ----

@pytest.mark.parametrize("probe", [
    {k: v for k, v in flat_decision().items() if k != "position"},   # missing req
    dict(flat_decision(), extra_field=1),                            # extra prop
    dict(flat_decision(), position="hedge"),                         # bad enum
    "just a string", ["list"], None, 42,                             # non-objects
    dict(flat_decision(), watch_condition="watch BTC closely"),      # malformed wc
    dict(long_decision(P), invalidation={"timeframe": "1h_close"}),  # malformed inv
    dict(long_decision(P), size_usd=float("nan")),                   # NaN
    dict(long_decision(P), stop_loss=float("inf")),                  # Infinity
    dict(long_decision(P), invalidation={"timeframe": "1m_intrabar",
                                         "operator": "price_at_or_below",
                                         "level": float("nan")}),    # NaN level
    dict(long_decision(P), invalidation=dict(long_decision(P)["invalidation"],
                                             hint="extra")),         # nested extra
])
def test_schema_probes_rejected_without_crash(probe):
    reasons = decisions.schema_validate(probe)
    assert reasons and all(isinstance(r, str) for r in reasons)


def test_schema_probe_through_coordinator_no_crash(accounts, snapshots, cfg):
    """Scalar + missing-position probes flow through the full path: rejected
    attempt + retry, never an AttributeError (regression)."""
    script = {"btc_haiku_raw": ["scalar", {"position": "flat"}],
              "eth_haiku_raw": [{k: v for k, v in flat_decision().items()
                                 if k != "position"}, flat_decision()]}
    ledger, archive, _, caller = run_prod(accounts, snapshots, cfg, script)
    ab = {e["pair"]: e for e in ledger if e["status"] == "PAIR_ABORTED"}
    assert "btc_haiku" in ab                     # scalar then still-invalid
    assert "eth_haiku" not in ab                 # corrected on retry
    recs = [a for a in archive if a["account_id"] == "btc_haiku_raw"]
    assert recs[0]["schema_result"] == "invalid"
    assert recs[0]["raw_response"] == '"scalar"'


def test_schema_invalid_retry_corrected_commits(accounts, snapshots, cfg):
    p = snapshots["SOL"]["P_T"]
    script = {"sol_opus_raw": [dict(long_decision(p), position="hedge"),
                               long_decision(p, 2000)]}
    ledger, archive, _, _ = run_prod(accounts, snapshots, cfg, script)
    assert all(e["status"] == "PAIR_COMMITTED" for e in ledger)


# ---- 2. executed flags only after pair commit ----

def test_valid_twin_not_executed_when_pair_aborts(accounts, snapshots, cfg):
    p = snapshots["BTC"]["P_T"]
    bad = dict(long_decision(p), size_usd=-1)
    script = {"btc_haiku_ta": [bad, bad],
              "btc_haiku_raw": long_decision(p, 2000)}   # raw VALID, ta fails 2x
    store = store_with(accounts)
    ledger, archive, _, _ = run_prod(accounts, snapshots, cfg, script, store=store)
    ab = [e for e in ledger if e["pair"] == "btc_haiku"][0]
    assert ab["status"] == "PAIR_ABORTED" and ab["caused_by_arm"] == "ta"
    raw_recs = [a for a in archive if a["account_id"] == "btc_haiku_raw"]
    assert raw_recs[0]["semantic_validation_result"] == "valid"
    assert raw_recs[0]["became_executed_decision"] is False       # NOT executed
    led = persistence.read_ledger(store + "/ledger.jsonl")
    assert not any(e.get("e") == "executed_attempt" and e["pair"] == "btc_haiku"
                   for e in led)
    # committed pairs DO get immutable execution-link events
    assert any(e.get("e") == "executed_attempt" for e in led)
    # durable attempt files always carry false (immutable; links are separate)
    path = persistence.attempt_path(store, raw_recs[0])
    assert json.load(open(path))["became_executed_decision"] is False


# ---- 3. durable prompt archive before any request ----

def test_prompts_on_disk_before_first_call_and_reused(accounts, snapshots, cfg):
    store = store_with(accounts)
    with pytest.raises(recovery.CrashError):
        run_prod(accounts, snapshots, cfg, None, store=store,
                 crash_at="after_prompts")
    d = os.path.join(store, "prompts", f"v1-ALL-{T0}")
    files = sorted(os.listdir(d))
    assert len(files) == 18                       # all archived pre-request
    archived = {f[:-5]: json.load(open(os.path.join(d, f))) for f in files}
    seen = {}
    def caller(aid, system, user, retry):
        seen.setdefault(aid, user)
        return flat_decision()
    recovery.recover(store)
    # recovery aborts (predeclared rule) — rerun a FRESH boundary store to
    # check byte-identical reuse instead:
    store2 = store_with(state.init_accounts())
    shutil.copytree(os.path.join(store, "prompts"),
                    os.path.join(store2, "prompts"))
    recovery.run_checkpointed(T0, snapshots, caller, cfg, store2)
    for aid, user in seen.items():
        assert archived[aid]["user"] == user      # archived bytes reused


def test_prompt_archive_failure_means_zero_calls(accounts, snapshots, cfg,
                                                 monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(persistence, "write_prompt_archive", boom)
    caller = ScriptedCaller({})
    with pytest.raises(recovery.PromptArchiveError):
        run_prod(accounts, snapshots, cfg, caller)
    assert caller.calls == []


def test_terminal_account_recorded_not_called(accounts, snapshots, cfg):
    accounts["btc_opus_raw"]["terminal"] = True
    store = store_with(accounts)
    run_prod(accounts, snapshots, cfg, None, store=store)
    e = json.load(open(os.path.join(store, "prompts", f"v1-ALL-{T0}",
                                    "btc_opus_raw.json")))
    assert e["not_called"] == "terminal"


# ---- 4. freshness regressions (stale-but-contiguous previously passed) ----

def _shift(cands, cut):
    return [c for c in cands if c["t"] < cut]


def test_stale_hourly_daily_1m_rejected():
    k1m, k1h, k1d = load_fix("BTC", "1m"), load_fix("BTC", "1h"), load_fix("BTC", "1d")
    with pytest.raises(marketdata.DataUnavailable, match="hourly: stale"):
        marketdata.build_snapshot("BTC", k1m, _shift(k1h, T0 - 2 * HOUR), k1d, T0)
    with pytest.raises(marketdata.DataUnavailable, match="daily: stale"):
        marketdata.build_snapshot("BTC", k1m, k1h, _shift(k1d, T0 - 2 * 86400), T0)
    with pytest.raises(marketdata.DataUnavailable, match="1m: stale"):
        marketdata.build_snapshot("BTC", _shift(k1m, T0 - 2 * MIN), k1h, k1d, T0)
    marketdata.build_snapshot("BTC", k1m, k1h, k1d, T0)   # fresh passes


def test_malformed_candles_rejected():
    k1m, k1h, k1d = load_fix("BTC", "1m"), list(load_fix("BTC", "1h")), load_fix("BTC", "1d")
    k1h = [dict(c) for c in k1h]
    for c in k1h:
        if c["t"] == T0 - HOUR:                            # inside the window
            c["h"] = 0.0                                   # non-positive price
    with pytest.raises(marketdata.DataUnavailable):
        marketdata.build_snapshot("BTC", k1m, k1h, k1d, T0)


# ---- 5/6. replay interval + ten-hour policy ----

def sol_candles(a, b):
    return [c for c in marketdata.to_dec(load_fix("SOL", "1m")) if a <= c["t"] < b]


@pytest.mark.parametrize("unresolved,expect", [
    (35940, "CATCHUP_REQUIRED"),      # 9h59m
    (36000, "CATCHUP_REQUIRED"),      # exactly 10h — NOT terminated
    (36060, "COIN_TERMINATED"),       # >10h
])
def test_ten_hour_policy(accounts, snapshots, cfg, unresolved, expect):
    store = store_with(accounts)
    spec = {"SOL": {"start": T0, "end": T0 + unresolved, "candles": []}}
    run_prod(accounts, snapshots, cfg, None, store=store, replay_spec=spec)
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["replay_state"]["SOL"]["status"] == expect
    assert meta["coin_terminated"].get("SOL", False) == (expect == "COIN_TERMINATED")
    led = persistence.read_ledger(store + "/ledger.jsonl")
    assert any(e.get("replay") and e["replay"][0]["e"] == expect for e in led)


def test_empty_and_wrong_interval_rejected(accounts, snapshots, cfg):
    for cands in ([], sol_candles(T0 + 300, T0 + 600),          # missing first
                  sol_candles(T0, T0 + 300),                    # missing last
                  sol_candles(T0 - 600, T0)):                   # wrong interval
        store = store_with(state.init_accounts())
        run_prod(state.init_accounts() if False else json.loads("null") or
                 state.init_accounts(), snapshots, cfg, None, store=store,
                 replay_spec={"SOL": {"start": T0, "end": T0 + 600,
                                      "candles": cands}})
        _, meta = persistence.load_state(store + "/state.json")
        assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"


def test_catchup_success_before_limit(accounts, snapshots, cfg):
    store = store_with(accounts)
    gapped = sol_candles(T0, T0 + 180) + sol_candles(T0 + 240, T0 + 600)
    run_prod(accounts, snapshots, cfg, None, store=store,
             replay_spec={"SOL": {"start": T0, "end": T0 + 600, "candles": gapped}})
    _, meta = persistence.load_state(store + "/state.json")
    wm = meta["replay_watermark"]["SOL"]
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"
    assert wm == T0 + 120                          # last trustworthy watermark
    # later run supplies the complete missing interval => catch-up succeeds
    a2, m2 = persistence.load_state(store + "/state.json")
    led2, _, _ = recovery.run_checkpointed(
        T0 + HOUR, snapshots, ScriptedCaller({}), cfg, store,
        replay_spec={"SOL": {"start": T0, "end": T0 + 600,
                             "candles": sol_candles(T0 + 180, T0 + 600)}})
    _, m3 = persistence.load_state(store + "/state.json")
    assert m3["replay_state"]["SOL"]["status"] == "REPLAY_COMPLETE"
    assert not m3["coin_terminated"].get("SOL")


# ---- 7. transport attempts: 1 initial + 3 retries = 4 total ----

def test_four_total_transport_attempts(accounts, snapshots, cfg):
    errs = [rounds.TransportError("boom")] * 10
    script = {"eth_sonnet_raw": errs}
    store = store_with(accounts)
    ledger, archive, _, caller = run_prod(accounts, snapshots, cfg, script,
                                          store=store)
    calls = [c for c in caller.calls if c["id"] == "eth_sonnet_raw"]
    assert len(calls) == 4                         # exactly 4 total attempts
    recs = [a for a in archive if a["account_id"] == "eth_sonnet_raw"]
    assert len(recs) == 4
    assert all(r["transport_error_category"] for r in recs)
    ab = [e for e in ledger if e["pair"] == "eth_sonnet"][0]
    assert ab["reason"] == "transport_failure"


# ---- 8. true bounded concurrency ----

class ConcurrencyCaller:
    def __init__(self, hold_until=6):
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.calls = []
        self.gate = threading.Event()
        self.hold_until = hold_until

    def __call__(self, aid, system, user, retry):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(aid)
            if self.active >= self.hold_until:
                self.gate.set()
        self.gate.wait(timeout=5)
        with self.lock:
            self.active -= 1
        return flat_decision()


def test_concurrency_peak_equals_limit_never_exceeds(accounts, snapshots, cfg):
    caller = ConcurrencyCaller(hold_until=6)
    ledger, *_ = run_prod(accounts, snapshots, cfg, caller)
    assert caller.peak == 6                        # reaches the limit
    assert caller.peak <= cfg["collection"]["concurrency_max_simultaneous_requests"]
    # twins entered the same wave: adjacent 6-call windows contain both arms
    first_wave = set(caller.calls[:6])
    for aid in list(first_wave):
        twin = (aid[:-4] + "_ta") if aid.endswith("_raw") else (aid[:-3] + "_raw")
        assert twin in first_wave


def test_shuffled_completion_order_identical_results(accounts, snapshots, cfg):
    import random

    def make_caller(seed):
        rng = random.Random(seed)
        lock = threading.Lock()
        def caller(aid, system, user, retry):
            import time as _t
            with lock:
                delay = rng.uniform(0, 0.01)
            _t.sleep(delay)
            return flat_decision()
        return caller
    hashes = []
    for seed in (1, 2):
        accts = state.init_accounts()
        store = store_with(accts)
        run_prod(accts, snapshots, cfg, make_caller(seed), store=store)
        a, m = persistence.load_state(store + "/state.json")
        hashes.append(json.dumps(persistence._enc_all(a), sort_keys=True,
                                 default=str))
    assert hashes[0] == hashes[1]


# ---- 9. hard global deadline ----

class DeadlineCaller:
    """Advances a fake clock when watched accounts complete. With
    `rendezvous`, ALL watched accounts must be in-flight (inside the caller,
    i.e. past their budget check) before the clock advances — this removes
    the race where one watched twin's completion time starved the other
    twin's budget check via the shared clock (Ruling 012.3 determinism)."""

    def __init__(self, clockbox, finish_at, rendezvous=False):
        self.clockbox = clockbox
        self.finish_at = finish_at
        self.lock = threading.Lock()
        self.barrier = (threading.Barrier(len(finish_at), timeout=30)
                        if rendezvous and len(finish_at) > 1 else None)

    def __call__(self, aid, system, user, retry):
        if aid in self.finish_at:
            if self.barrier is not None:
                self.barrier.wait()
            with self.lock:
                self.clockbox[0] = max(self.clockbox[0], self.finish_at[aid])
        return flat_decision()


@pytest.mark.parametrize("rep", range(3))
@pytest.mark.parametrize("finish,ok", [(719.0, True), (720.0, False), (721.0, False)])
def test_deadline_boundaries(accounts, snapshots, cfg, finish, ok, rep):
    """Both twins finish at exactly `finish` (rendezvous: both are past their
    budget checks before the clock moves) — deterministic across repeats."""
    clockbox = [0.0]
    caller = DeadlineCaller(clockbox, {"btc_haiku_raw": finish,
                                       "btc_haiku_ta": finish},
                            rendezvous=True)
    ledger, *_ = run_prod(accounts, snapshots, cfg, caller,
                          clock=lambda: clockbox[0])
    pair = [e for e in ledger if e["pair"] == "btc_haiku"][0]
    if ok:
        assert pair["status"] == "PAIR_COMMITTED"
    else:
        assert pair["status"] == "PAIR_ABORTED"
        assert pair["reason"] == "deadline_exceeded"


def test_one_twin_late_aborts_only_that_pair(accounts, snapshots, cfg):
    clockbox = [0.0]
    caller = DeadlineCaller(clockbox, {"btc_haiku_ta": 725.0})
    ledger, *_ = run_prod(accounts, snapshots, cfg, caller,
                          clock=lambda: clockbox[0])
    st = {e["pair"]: e["status"] for e in ledger}
    assert st["btc_haiku"] == "PAIR_ABORTED"
    # pairs resolved in the same wave before the late twin remain valid;
    # later waves abort on the exhausted budget — deterministic either way
    assert any(s == "PAIR_COMMITTED" for p, s in st.items() if p != "btc_haiku")


# ---- 10/12. outbox atomicity + termination persistence ----

@pytest.mark.parametrize("point", ["after_checkpoint", "between_publish_and_mark"])
def test_outbox_crash_recovery_consistent(accounts, snapshots, cfg, point):
    store = store_with(accounts)
    with pytest.raises(recovery.CrashError):
        run_prod(accounts, snapshots, cfg, None, store=store, crash_at=point)
    recovery.recover(store)
    a2, _ = persistence.load_state(store + "/state.json")
    recovery.run_checkpointed(T0, snapshots, ScriptedCaller({}), cfg, store)
    _, meta = persistence.load_state(store + "/state.json")
    led = persistence.read_ledger(store + "/ledger.jsonl")
    pub = [e for e in led if e.get("status") and e.get("pair")]
    # regression: every finalized pair has EXACTLY ONE published ledger record
    for pid, status in meta["finalized_pairs"].items():
        matches = [e for e in pub if e["pair"] == pid]
        assert len(matches) == 1 and matches[0]["status"] == status
    assert meta["outbox"] == []                    # nothing pending


def test_termination_event_persisted_to_ledger(accounts, snapshots, cfg):
    store = store_with(accounts)
    run_prod(accounts, snapshots, cfg, None, store=store,
             replay_spec={"SOL": {"start": T0, "end": T0 + 11 * HOUR,
                                  "candles": []}})
    led = persistence.read_ledger(store + "/ledger.jsonl")
    assert any(e.get("replay") and e["replay"][0]["e"] == "COIN_TERMINATED"
               for e in led)


# ---- 11. durable attempt files ----

def test_attempt_files_durable_and_duplicate_safe(accounts, snapshots, cfg):
    store = store_with(accounts)
    _, archive, _, _ = run_prod(accounts, snapshots, cfg, None, store=store)
    rec = archive[0]
    path = persistence.attempt_path(store, rec)
    assert os.path.exists(path)
    before = open(path).read()
    persistence.write_attempt(store, dict(rec, raw_response="ALTERED"))
    assert open(path).read() == before             # duplicate detected, kept first


# ---- 13. code-integrity manifest ----

def test_manifest_covers_code_and_detects_any_byte(tmp_path, monkeypatch):
    dst = tmp_path / "tree"
    for d in ("engine", "scripts", "prompts", "schemas", "config",
              "deploy"):
        shutil.copytree(os.path.join(config.ROOT, d), dst / d)
    monkeypatch.setattr(config, "ROOT", str(dst))
    m = config.build_manifest()
    assert any(p.startswith("engine/") for p in m["files"])
    assert "schemas/v1/records.schema.json" in m["files"]
    for victim in ("engine/execution.py", "schemas/v1/decision.schema.json",
                   "prompts/v1/system.txt", "config/v1/experiment.json"):
        p = dst / victim
        orig = p.read_bytes()
        p.write_bytes(orig + b"#x")
        with pytest.raises(config.IntegrityError):
            config.verify_integrity(m)
        p.write_bytes(orig)
    assert config.verify_integrity(m)


# ---- 14. configuration is authoritative ----

def test_params_mismatch_halts(accounts, snapshots, cfg):
    bad = json.loads(json.dumps(cfg))
    bad["parameters"]["fee_rate"] = "0.0010"
    with pytest.raises(state.ParamsMismatch):
        run_prod(accounts, snapshots, bad, None)
    bad2 = json.loads(json.dumps(cfg))
    bad2["parameters"]["max_leverage"] = "3"
    with pytest.raises(state.ParamsMismatch):
        state.verify_params(bad2)
    assert state.verify_params(cfg)


# ---- 15. append-only lifecycle history (three sequential lifecycles) ----

def test_three_lifecycles_all_queryable():
    a = state.new_account("BTC", "h", "raw")
    for i in range(3):
        d = long_decision(P, 2000 + i)
        d["invalidation"] = {"timeframe": "1m_intrabar",
                             "operator": "price_at_or_below",
                             "level": float(P * Decimal("0.6"))}
        d["stop_loss"] = None
        execution.apply_decision(a, d, P, i * 1000)
        if i == 0:                                 # trigger the first lifecycle
            replay.replay([a], [{"t": i * 1000 + 60, "o": P, "h": P,
                                 "l": P * Decimal("0.55"), "c": P,
                                 "v": Decimal(1)}], [])
        if state.side(a) != "flat":
            execution.apply_decision(a, flat_decision(), P, i * 1000 + 500)
    assert len(a["lifecycles"]) == 3
    ids = [lc["lifecycle_id"] for lc in a["lifecycles"]]
    assert len(set(ids)) == 3
    assert a["lifecycles"][0]["triggered"] is not None      # history preserved
    assert all(tr["lifecycle_id"] in ids for tr in a["trades"])
    from engine import metrics
    resp = metrics.invalidation_response({"x": a}, {})
    assert len(resp) >= 1


# ---- 16. watch-condition timeframe ----

def test_hourly_watch_not_triggered_by_wick():
    a = state.new_account("BTC", "h", "raw")
    d = dict(flat_decision(), watch_condition={"timeframe": "1h_close",
                                               "operator": "price_at_or_above",
                                               "level": 105})
    execution.apply_decision(a, d, P, 0)
    wick = [{"t": t, "o": P, "h": Decimal("106"), "l": P, "c": P,
             "v": Decimal(1)} for t in range(0, 3540, 60)]
    replay.replay([a], wick, [])
    assert a["watch"]["triggered"] is None          # intrahour wick ignored
    replay.replay([a], [{"t": 3540, "o": P, "h": Decimal("106"), "l": P,
                         "c": Decimal("106"), "v": Decimal(1)}], [])
    assert a["watch"]["triggered"] is not None      # completed close triggers


# ---- 17. state-corruption detection ----

def test_state_corruption_matrix(tmp_path):
    accounts = state.init_accounts()
    path = str(tmp_path / "s.json")
    persistence.save_state(path, accounts, {"boundary": 1})
    good = open(path).read()
    cases = {
        "dup_keys": good.replace('"boundary": 1', '"boundary": 1, "boundary": 2', 1),
        "truncated": good[:len(good) // 2],
        "altered_balance": good.replace('"10000.00"', '"99999.00"', 1),
    }
    for name, text in cases.items():
        open(path, "w").write(text)
        with pytest.raises(persistence.StateCorruption):
            persistence.load_state(path)
    payload = json.loads(good)
    del payload["accounts"]["btc_haiku_raw"]
    del payload["meta"]["_checksum"]
    open(path, "w").write(json.dumps(payload))
    with pytest.raises(persistence.StateCorruption):
        persistence.load_state(path, expect_full_roster=True)
    payload2 = json.loads(good)
    payload2["accounts"]["btc_haiku_raw"]["qty"] = "0.1234567"   # precision
    del payload2["meta"]["_checksum"]
    open(path, "w").write(json.dumps(payload2))
    with pytest.raises(persistence.StateCorruption):
        persistence.load_state(path)
    payload3 = json.loads(good)
    payload3["accounts"]["btc_haiku_raw"]["stop"] = "95"         # flat + stop
    del payload3["meta"]["_checksum"]
    open(path, "w").write(json.dumps(payload3))
    with pytest.raises(persistence.StateCorruption):
        persistence.load_state(path)
