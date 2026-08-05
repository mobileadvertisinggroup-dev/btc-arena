"""Mentor Ruling 019 remediation regressions: authoritative 1m marks
independent of prompt data, crash-recoverable initial provisioning, strict
1m candle-value validation with source precision, post-fetch T+30 budget
enforcement, and operational cleanup. All offline and deterministic."""
import io
import json
import os
import time
from decimal import Decimal

import pytest

from conftest import T0, ScriptedCaller, long_decision
from engine import (config, dashboard, marketdata, official, persistence,
                    recovery)
from test_official import direct, provisioned_official, run_official
from test_ruling010 import PilotClock, digests, parse_payload
from test_ruling011 import _sol_price
from test_ruling016 import (_load_script, _passing_report, mk_candles,
                            snapshots_at)
from test_ruling018 import _kraken_stub, _open_sol

HOUR = 3600
T1, T2 = T0 + HOUR, T0 + 2 * HOUR


# ---- 019.1 authoritative 1m mark independent of prompt data ----

@pytest.mark.parametrize("fail_interval", [60, 1440])
def test_prompt_outage_still_persists_the_valid_1m_mark(cfg, monkeypatch,
                                                        fail_interval):
    """The mentor's reproduced hole: open position + valid 1m + failed
    prompt snapshot must keep the REAL T-60 mark, real equity history, and
    real dashboard P&L — with zero model calls for the coin."""
    mod = _load_script("run_official_14d")
    p = float(_sol_price())
    sol_1m = mk_candles(T0, 120, p)                  # flat: position survives
    monkeypatch.setattr(mod, "kraken_ohlc", _kraken_stub(
        sol_1m=sol_1m, fail={("SOLUSD", fail_interval)}))
    snap, spec = mod.fetch_market("SOL", T1, False)  # production split path
    assert snap is None
    store = _open_sol(cfg)
    caller = ScriptedCaller({})
    snaps = snapshots_at(T1)
    snaps["SOL"] = snap
    recovery.run_checkpointed(T1, snaps, caller, cfg, store,
                              pre_replay_spec={"SOL": spec})
    accounts, meta = persistence.load_state(store + "/state.json")
    a = accounts["sol_haiku_raw"]
    assert a["qty"] != 0                             # position survived
    expected_mark = str(sol_1m[59]["c"])             # exact T1-60 close
    assert meta["marks"]["SOL"] == expected_mark     # REAL mark persisted
    # equity history carries the real marked equity, never null
    pt = meta["equity_history"]["sol_haiku_raw"][-1]
    assert pt["T"] == T1 and pt["equity"] is not None
    from engine import state as state_mod
    assert Decimal(pt["equity"]) == state_mod.equity_at(
        a, Decimal(expected_mark))
    # dashboard payload: correct mark and non-null P&L
    manifest = config.build_manifest()
    pl = dashboard.payload(accounts, [], {c: None for c in ("BTC", "ETH",
                                                            "SOL")},
                           None, manifest, cfg,
                           marks={c: (meta["marks"] or {}).get(c)
                                  for c in ("BTC", "ETH", "SOL")})
    row = {r["id"]: r for r in pl["coins"]["SOL"]["accounts"]}["sol_haiku_raw"]
    assert row["mark"] == expected_mark
    assert row["unrealized_pnl"] is not None and row["equity"] is not None
    assert not [c for c in caller.calls if c["id"].startswith("sol")]


def test_true_1m_failure_fabricates_no_mark(cfg):
    store = _open_sol(cfg)
    snaps = snapshots_at(T1)
    snaps["SOL"] = None
    recovery.run_checkpointed(
        T1, snaps, ScriptedCaller({}), cfg, store,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": []}})
    _, meta = persistence.load_state(store + "/state.json")
    assert meta["marks"]["SOL"] is None              # nothing fabricated
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"


def test_final_boundary_uses_valid_1m_mark_despite_prompt_outage(cfg,
                                                                 monkeypatch):
    """The FINAL official boundary reports real marked equity from the 1m
    P_T even when the hourly/daily prompt data is down."""
    mod = _load_script("run_official_14d")
    p = float(_sol_price())
    calls = {"n": 0}
    real_stub = _kraken_stub(sol_1m=mk_candles(T0 - HOUR, 240, p))

    def stub(pair, interval_min):
        if pair == "SOLUSD" and interval_min in (60, 1440):
            calls["n"] += 1
            if calls["n"] > 2:                       # b1 ok; final b2 fails
                raise RuntimeError("prompt feed down")
        return real_stub(pair, interval_min)
    monkeypatch.setattr(mod, "kraken_ohlc", stub)
    store = provisioned_official(n=2)
    vc = PilotClock(float(T0) - 60)
    dp, pub_dir = direct(vc)
    d = long_decision(p, 2000)
    official.run_official(store, cfg, ScriptedCaller({"sol_haiku_raw": d}),
                          mod.fetch_market, dp, vc, vc.sleep)
    pl = parse_payload(open(os.path.join(pub_dir, "live_payload.js")).read())
    row = {r["id"]: r for r in pl["coins"]["SOL"]["accounts"]}["sol_haiku_raw"]
    assert pl["marks"]["SOL"]["price"] is not None   # final 1m mark used
    assert row["equity"] is not None                 # final marked equity


# ---- 019.2 crash-recoverable initial provisioning ----

def _arm_args(tmp_path, n=2, name="r19.json"):
    eng, site = digests()
    path, sha = _passing_report(tmp_path, eng, site, name=name)
    act = {"approved": "YES-OFFICIAL-RUN-APPROVED", "engine_digest": eng,
           "site_digest": site, "start_utc": T0, "total": n,
           "preflight": {"report_path": path, "report_sha256": sha}}
    blob = open(path, "rb").read()
    return act, "ab" * 32, eng, site, blob


@pytest.mark.parametrize("crash_at", ["after_provision",
                                      "after_preflight_copy"])
def test_crash_mid_transaction_recovers_to_armed_off(cfg, tmp_path, crash_at):
    import tempfile
    act, sha, eng, site, blob = _arm_args(tmp_path, name=f"{crash_at}.json")
    store = tempfile.mkdtemp(prefix="arena-r19-")
    persistence.save_state(os.path.join(store, "state.json"),
                           __import__("engine.state",
                                      fromlist=["init_accounts"])
                           .init_accounts(), {"boundary": None})
    with pytest.raises(recovery.CrashError):
        official.arm_store(store, act, sha, eng, site, report_blob=blob,
                           crash_at=crash_at)
    # zero boundary work; accepted marker absent
    assert not os.path.exists(os.path.join(
        store, official.ACCEPTED_ACTIVATION_NAME))
    assert official.reconcile_incomplete_provisioning(store) \
        == "rolled_back_incomplete"                  # pristine proven inside
    official.verify_pristine_official_state(store)
    # clean re-arm succeeds completely
    sched = official.arm_store(store, act, sha, eng, site, report_blob=blob)
    assert sched["total"] == 2
    assert official.reconcile_incomplete_provisioning(store) == "committed"
    assert official.verify_durable_trust(store, time.time(),
                                         waive_freshness=True)


def test_crash_before_binding_recovers(cfg, tmp_path, monkeypatch):
    import tempfile
    act, sha, eng, site, blob = _arm_args(tmp_path, name="nobind.json")
    store = tempfile.mkdtemp(prefix="arena-r19b-")

    def boom(*a, **k):
        raise recovery.CrashError("before_binding")
    monkeypatch.setattr(official, "write_activation_binding", boom)
    with pytest.raises(recovery.CrashError):
        official.arm_store(store, act, sha, eng, site, report_blob=blob)
    monkeypatch.undo()
    assert official.reconcile_incomplete_provisioning(store) \
        == "rolled_back_incomplete"
    sched = official.arm_store(store, act, sha, eng, site, report_blob=blob)
    assert official.verify_durable_trust(store, time.time(),
                                         waive_freshness=True)
    assert sched["start"] == T0


def test_tampered_blob_refuses_archive(cfg, tmp_path):
    import tempfile
    act, sha, eng, site, blob = _arm_args(tmp_path, name="tamper.json")
    store = tempfile.mkdtemp(prefix="arena-r19t-")
    with pytest.raises(official.TrustRecordError):
        official.arm_store(store, act, sha, eng, site,
                           report_blob=blob + b" ")
    assert not os.path.exists(os.path.join(
        store, official.ACCEPTED_ACTIVATION_NAME))


def test_started_run_never_rolled_back_by_incomplete_check(cfg):
    store = provisioned_official(n=2)                # no accepted marker
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    with pytest.raises(recovery.CrashError):
        run_official(store, cfg, ScriptedCaller({}), dp, vc,
                     crash_at="after_one_pair")
    assert official.reconcile_incomplete_provisioning(store) == "started"
    from engine import pilot
    assert pilot.load_schedule(store)["boundaries"][0] == T0   # intact


def test_runner_wires_incomplete_recovery_and_trust_before_ready():
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    assert "reconcile_incomplete_provisioning" in src
    arm_block = src[src.index("official.arm_store("):]
    assert arm_block.index('validate_trust_or_halt("unstarted")') \
        < arm_block.index("run_official(")           # trust BEFORE READY


# ---- 019.3 strict candle-value validation + source precision ----

BAD_CANDLES = {
    "negative_price": {"l": Decimal("-1")},
    "zero_price": {"c": Decimal("0")},
    "nonfinite": {"h": Decimal("NaN")},
    "impossible_high": {"h": Decimal("1"), "l": Decimal("2"),
                        "o": Decimal("2"), "c": Decimal("2")},
    "negative_volume": {"v": Decimal("-5")},
    "misaligned": {"t": T0 + 30},
}


@pytest.mark.parametrize("case", sorted(BAD_CANDLES))
def test_malformed_candle_values_rejected(case):
    c = {"t": T0, "o": Decimal("10"), "h": Decimal("10"),
         "l": Decimal("10"), "c": Decimal("10"), "v": Decimal("1")}
    c.update(BAD_CANDLES[case])
    with pytest.raises(marketdata.DataUnavailable):
        marketdata.validate_candle_values(c)
    candles = mk_candles(T0, 60, 10)
    candles[30] = dict(candles[30], **BAD_CANDLES[case])
    with pytest.raises(marketdata.DataUnavailable):
        marketdata.validate_1m_coverage(candles, T0, T1)


def test_duplicate_candles_rejected():
    candles = mk_candles(T0, 60, 10)
    candles.insert(31, dict(candles[30]))            # duplicate timestamp
    with pytest.raises(marketdata.DataUnavailable):
        marketdata.validate_1m_coverage(candles, T0, T1)


def test_malformed_candle_never_executes_a_stop(cfg):
    """A stop-crossing candle with an impossible OHLC must NOT execute — it
    becomes the gap."""
    p = float(_sol_price())
    store = _open_sol(cfg)
    candles = mk_candles(T0, 60, p, dip_to=p * 0.5, at=30)
    candles[30]["h"] = Decimal("0")                  # malformed (would-hit)
    recovery.run_checkpointed(
        T1, snapshots_at(T1), ScriptedCaller({}), cfg, store,
        pre_replay_spec={"SOL": {"start": T0, "end": T1, "candles": candles}})
    accounts, meta = persistence.load_state(store + "/state.json")
    assert accounts["sol_haiku_raw"]["trades"] == []     # nothing executed
    assert accounts["sol_haiku_raw"]["qty"] != 0
    assert meta["replay_state"]["SOL"]["status"] == "CATCHUP_REQUIRED"
    assert meta["replay_next_required"]["SOL"] == T0 + 30 * 60


def test_kraken_source_precision_preserved(monkeypatch):
    mod = _load_script("run_official_14d")
    precise = "67123.123456789012345"
    rows = [[T0 - 60 + i * 60, precise, precise, precise, precise, "0",
             "1.000000000000000001"] for i in range(3)]
    payload = json.dumps({"result": {"XXBTZUSD": rows, "last": T0}}).encode()

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda url, timeout=0: FakeResp(payload))
    out = mod.kraken_ohlc("XBTUSD", 1)
    assert out[0]["o"] == precise                    # strings carried through
    spec_candles = __import__("engine.marketdata",
                              fromlist=["to_dec"]).to_dec(out)
    assert str(spec_candles[0]["o"]) == precise      # exact Decimal
    assert str(spec_candles[0]["v"]) == "1.000000000000000001"
    assert Decimal(precise) != Decimal(str(float(precise)))   # float mangles


# ---- 019.4 post-fetch T+30 budget ----

def test_fetch_completing_after_T30_blocks_prompts_keeps_replay(cfg):
    store = provisioned_official(n=2)
    vc = PilotClock(float(T0) - 60)
    dp, _ = direct(vc)
    p = float(_sol_price())
    from test_ruling010 import fake_fetch

    def slow_sol_fetch(coin, T, first):
        snap, spec = fake_fetch(coin, T, first)
        if coin == "SOL" and T == T1:
            spec = {"start": T0, "end": T1,
                    "candles": mk_candles(T0, 60, p)}
            vc.t = T1 + 40.0                         # completes AFTER T+30
        return snap, spec
    caller = ScriptedCaller({"sol_haiku_raw": long_decision(p, 2000)})
    official.run_official(store, cfg, caller, slow_sol_fetch, dp, vc,
                          vc.sleep)
    _, meta = persistence.load_state(os.path.join(store, "state.json"))
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    from engine import prompts
    b2_sol = [e for e in ledger if e.get("pair") == "sol_haiku"
              and e.get("round_id") == prompts.round_id("SOL", T1)]
    # late prompt snapshot never used: boundary-2 SOL pairs blocked
    sol_calls_b2 = [c for c in caller.calls[18:]
                    if c["id"].startswith("sol")]
    assert sol_calls_b2 == []
    # but the valid 1m data was RETAINED: replay complete + mark present
    assert meta["replay_state"]["SOL"]["status"] == "REPLAY_COMPLETE"
    assert meta["replay_next_required"]["SOL"] == T1
    assert meta["marks"]["SOL"] is not None
    assert b2_sol and b2_sol[0]["reason"] == "DATA_UNAVAILABLE"


# ---- 019.5 operational cleanup ----

def test_started_restart_requires_api_key_before_anything():
    src = open(os.path.join(config.ROOT, "scripts",
                            "run_official_14d.py")).read()
    started = src[src.index('kind == "started"'):src.index("RESUMING")]
    assert "ANTHROPIC_API_KEY" in started            # checked BEFORE resume
    assert "CREDENTIAL HALT" in started


def test_deployment_doc_activation_and_env_guidance():
    doc = open(os.path.join(config.ROOT, "deploy", "DEPLOYMENT.md")).read()
    assert "--preflight-report" in doc and "--preflight-sha" in doc
    assert "set -a" in doc and "without printing" in doc.lower()
    assert "NTP" in doc


def test_verifier_checks_ntp_sync():
    mod = _load_script("verify_deployment")
    ok = mod.check_time_sync(run=lambda: "yes")
    assert [o for _, o, _ in ok] == [True]
    bad = mod.check_time_sync(run=lambda: "no")
    assert [o for _, o, _ in bad] == [False]
    src = open(os.path.join(config.ROOT, "scripts",
                            "verify_deployment.py")).read()
    assert "check_time_sync()" in src                # wired into main


def test_public_naming_is_ta_everywhere(cfg):
    from engine import state as state_mod
    manifest = config.build_manifest()
    pl = dashboard.payload(state_mod.init_accounts(), [],
                           {c: None for c in ("BTC", "ETH", "SOL")},
                           None, manifest, cfg)
    pair = pl["coins"]["BTC"]["pairs"][0]
    assert "ta" in pair and "feature" not in pair    # payload key renamed
    assert "TA arm" in cfg["arm_naming"]
    for rel in ("docs/prestart_payload.js", "docs/demo_payload.js"):
        text = open(os.path.join(config.ROOT, rel)).read()
        assert '"feature":' not in text and '"ta":' in text
    assert "Feature arm in public" not in open(
        os.path.join(config.ROOT, "engine", "state.py")).read()
