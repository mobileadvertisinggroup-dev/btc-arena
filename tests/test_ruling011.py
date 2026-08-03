"""Ruling 011: valid market marks (never cash-as-price), durable equity
history, THINKING/COMMITTED lifecycle publication with READY gate, public-URL
verification, site-manifest integrity, and the front-end payload contract."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from decimal import Decimal

import pytest

from conftest import T0, ScriptedCaller, load_fix, long_decision
from engine import (config, dashboard, marketdata, persistence, pilot,
                    publisher, state)
from test_ruling010 import (FakePublisher, fake_fetch, no_sleep, parse_payload,
                            provisioned_pilot, digests, run, FAR_FUTURE)

COINS = ("BTC", "ETH", "SOL")
MANIFEST_STUB = {"files": {}, "combined": "test"}


def _mk_account(coin, E, qty, entry):
    a = state.new_account(coin, "haiku", "raw")
    a["E"] = Decimal(E)
    a["qty"] = Decimal(qty)
    a["entry"] = Decimal(entry) if entry is not None else None
    if a["qty"]:
        a["stop"] = None
        a["tp"] = None
    return {a["id"]: a}


def _row(cfg, accounts, marks):
    pl = dashboard.payload(accounts, [], {c: None for c in COINS},
                           None, MANIFEST_STUB, cfg, marks=marks)
    coin = next(iter(accounts.values()))["coin"]
    return pl["coins"][coin]["accounts"][0]


# ---- 1. marks: market price or nothing — the mentor's exact probe ----

def test_probe_long_position_uses_market_mark_not_cash(cfg):
    accounts = _mk_account("BTC", "9999.00", "0.10000000", "60000")
    row = _row(cfg, accounts, {"BTC": "61000"})
    assert Decimal(row["equity"]) == Decimal("10099.00")
    assert Decimal(row["unrealized_pnl"]) == Decimal("100.00")
    assert Decimal(row["notional"]) == Decimal("6100.00")
    assert row["mark"] == "61000" and row["mark_unavailable"] is False


def test_short_position_mark(cfg):
    accounts = _mk_account("BTC", "9999.00", "-0.10000000", "60000")
    row = _row(cfg, accounts, {"BTC": "59000"})
    assert Decimal(row["equity"]) == Decimal("10099.00")
    assert Decimal(row["unrealized_pnl"]) == Decimal("100.00")
    assert Decimal(row["notional"]) == Decimal("5900.00")


def test_flat_account_exact_without_mark(cfg):
    accounts = _mk_account("BTC", "9876.54", "0", None)
    row = _row(cfg, accounts, {"BTC": None})
    assert Decimal(row["equity"]) == Decimal("9876.54")   # cash is exact
    assert row["unrealized_pnl"] == "0" and row["notional"] == "0"
    assert row["mark"] is None and row["mark_unavailable"] is True


def test_open_position_missing_mark_is_explicit_null_never_cash(cfg):
    accounts = _mk_account("BTC", "9999.00", "0.10000000", "60000")
    row = _row(cfg, accounts, {"BTC": None})
    assert row["equity"] is None                     # explicit unavailability
    assert row["unrealized_pnl"] is None
    assert row["notional"] is None
    assert row["total_return_pct"] is None
    assert row["mark_unavailable"] is True
    # the pre-Ruling-011 defect values must be impossible now:
    assert row["equity"] != "4998.9000000000"


# ---- 2. persisted marks + equity history through the real pilot ----

def _sol_price():
    snap = marketdata.build_snapshot("SOL", load_fix("SOL", "1m"),
                                     load_fix("SOL", "1h"),
                                     load_fix("SOL", "1d"), T0)
    return snap["P_T"]


def test_committed_payload_uses_persisted_boundary_mark(cfg):
    store = provisioned_pilot(n=1)
    p = _sol_price()
    caller = ScriptedCaller({"sol_haiku_raw": [long_decision(float(p), 2000)]})
    pub = FakePublisher()
    run(store, cfg, caller, pub)
    _, meta = persistence.load_state(os.path.join(store, "state.json"),
                                     expect_full_roster=True)
    assert meta["marks"]["SOL"] is not None and meta["marks_T"] == T0
    pl = pub.by_lifecycle("ROUND_COMMITTED")[0]
    row = [a for a in pl["coins"]["SOL"]["accounts"]
           if a["id"] == "sol_haiku_raw"][0]
    assert row["side"] == "long" and row["mark"] == meta["marks"]["SOL"]
    accounts, _ = persistence.load_state(os.path.join(store, "state.json"))
    expected = state.equity_at(accounts["sol_haiku_raw"],
                               Decimal(meta["marks"]["SOL"]))
    assert Decimal(row["equity"]) == expected
    assert pl["marks"]["SOL"] == {"price": meta["marks"]["SOL"], "T": T0,
                                  "stale": False, "unavailable": False}


def test_open_position_with_data_blocked_coin_publishes_null_equity(cfg):
    """Position opened at boundary 1; SOL data unavailable at boundary 2:
    the published equity is explicit null (stale/unavailable), never cash."""
    store = provisioned_pilot(n=2)
    p = _sol_price()

    def flaky(coin, T, first):
        if coin == "SOL" and T > T0:
            raise RuntimeError("sol feed down")
        return fake_fetch(coin, T, first)
    caller = ScriptedCaller({"sol_haiku_raw": [long_decision(float(p), 2000)]})
    pub = FakePublisher()
    pilot.run_pilot(store, cfg, caller, flaky, pub,
                    clock=lambda: FAR_FUTURE, sleep=no_sleep)
    pl = pub.by_lifecycle("ROUND_COMMITTED")[1]
    row = [a for a in pl["coins"]["SOL"]["accounts"]
           if a["id"] == "sol_haiku_raw"][0]
    assert row["side"] == "long"                     # position survived
    assert row["equity"] is None and row["mark_unavailable"] is True
    assert pl["marks"]["SOL"]["unavailable"] is True
    # durable history: start + boundary1 (real mark) + boundary2 (explicit null)
    assert [pt["equity"] is None for pt in row["series"]] == [False, False, True]


def test_thinking_payload_marks_flagged_stale(cfg):
    store = provisioned_pilot(n=2)
    caller = ScriptedCaller({})
    pub = FakePublisher()
    run(store, cfg, caller, pub)
    think2 = pub.by_lifecycle("THINKING")[1]         # boundary 2 pre-request
    assert think2["published_boundary"] == T0 + 3600
    assert think2["marks"]["BTC"]["T"] == T0         # previous boundary's mark
    assert think2["marks"]["BTC"]["stale"] is True


def test_chart_receives_13_points_after_12_rounds(cfg):
    store = provisioned_pilot(n=12)
    pub = FakePublisher()
    run(store, cfg, ScriptedCaller({}), pub)
    pl = pub.by_lifecycle("ROUND_COMMITTED")[-1]
    for coin in COINS:
        for row in pl["coins"][coin]["accounts"]:
            series = row["series"]
            assert len(series) == 13                 # start + 12 boundaries
            assert series[0] == {"t": T0 - 3600, "equity": "10000.00",
                                 "fees": "0"}
            assert [pt["t"] for pt in series[1:]] == \
                [T0 + i * 3600 for i in range(12)]
            assert all(pt["equity"] is not None for pt in series)


# ---- 3. lifecycle: READY gate + THINKING before requests + COMMITTED after ----

def test_lifecycle_sequence_and_progress(cfg):
    store = provisioned_pilot(n=2)
    pub = FakePublisher()
    run(store, cfg, ScriptedCaller({}), pub)
    seq = [parse_payload(t)["round_lifecycle"] for t in pub.published]
    assert seq == ["READY", "THINKING", "ROUND_COMMITTED",
                   "THINKING", "ROUND_COMMITTED"]
    thinks = pub.by_lifecycle("THINKING")
    commits = pub.by_lifecycle("ROUND_COMMITTED")
    for i in (0, 1):
        assert thinks[i]["published_boundary"] == T0 + i * 3600
        assert thinks[i]["pilot_progress"]["done"] == i      # unchanged
        assert commits[i]["pilot_progress"]["done"] == i + 1  # advanced
        assert "lifecycle_notice" in thinks[i]


def test_thinking_published_before_any_model_request(cfg):
    store = provisioned_pilot(n=1)
    pub = FakePublisher()
    seen = []

    class GateCaller(ScriptedCaller):
        def __call__(self, aid, system, user, retry):
            log = publisher.read_log(store)
            seen.append(log.get(f"{T0}:thinking", {}).get("status"))
            return super().__call__(aid, system, user, retry)
    caller = GateCaller({})
    run(store, cfg, caller, pub)
    assert seen and all(s == "PUBLISHED" for s in seen)


def test_first_thinking_payload_fabricates_nothing(cfg):
    store = provisioned_pilot(n=1)
    pub = FakePublisher()
    run(store, cfg, ScriptedCaller({}), pub)
    think = pub.by_lifecycle("THINKING")[0]
    assert think["pilot_progress"] == {"done": 0, "total": 1}
    assert think["round_counts"] == {"PAIR_COMMITTED": 0, "PAIR_ABORTED": 0,
                                     "PAIR_TERMINAL_SPLIT": 0}
    for coin in COINS:
        for row in think["coins"][coin]["accounts"]:
            assert row["equity"] == "10000.00" and row["side"] == "flat"
            assert row["thesis"] is None and row["trades"] == []


def test_ready_transport_failure_blocks_all_model_calls(cfg):
    store = provisioned_pilot(n=1)
    caller = ScriptedCaller({})
    with pytest.raises(publisher.PublicationError):
        run(store, cfg, caller, FakePublisher(fail_times=1))
    assert caller.calls == []
    assert publisher.read_log(store)["ready"]["status"] == "FAILED"


# ---- 4. public-URL verification (production publisher logic, offline) ----

def _script_mod():
    path = os.path.join(config.ROOT, "scripts", "run_pilot_12h.py")
    spec = importlib.util.spec_from_file_location("run_pilot_12h_ut", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class VClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _remote_text(pub_id):
    return f'window.ARENA_LIVE = {json.dumps({"publication_id": pub_id})};\n'


def test_delayed_correct_public_content_becomes_published(cfg):
    mod = _script_mod()
    clock = VClock()
    served = ["", "", _remote_text("7:READY:0")]     # 404-ish, stale, correct

    def fetch():
        t = served.pop(0)
        if not t:
            raise OSError("HTTP 404")
        return t
    out = mod.poll_public("7:READY:0", fetch, clock, clock.sleep,
                          timeout=300, interval=15)
    assert "7:READY:0" in out
    assert clock.t == 30                             # two waits, then success


def test_incorrect_public_content_times_out_failed(cfg):
    mod = _script_mod()
    clock = VClock()

    def fetch():
        return _remote_text("stale:id:0")            # never the expected id
    with pytest.raises(publisher.PublicationError, match="stale"):
        mod.poll_public("7:READY:1", fetch, clock, clock.sleep,
                        timeout=300, interval=15)


def test_local_write_alone_is_not_publicly_published(cfg, tmp_path):
    """Even with the payload durably on local disk, an unreachable public URL
    must yield PublicationError (=> FAILED), never PUBLISHED."""
    mod = _script_mod()
    local = tmp_path / "live_payload.js"
    local.write_text(_remote_text("9:ROUND_COMMITTED:3"))

    def fetch():
        raise OSError("public URL unreachable")
    clock = VClock()
    with pytest.raises(publisher.PublicationError, match="timeout"):
        mod.poll_public("9:ROUND_COMMITTED:3", fetch, clock, clock.sleep,
                        timeout=60, interval=15)
    assert local.exists()                            # local file was irrelevant


def test_public_verification_failure_never_repeats_engine_execution(cfg):
    store = provisioned_pilot(n=1)
    caller = ScriptedCaller({})
    # READY + THINKING succeed; the committed publication fails public verify
    pub = FakePublisher(ok_first=2, fail_times=999)
    run(store, cfg, caller, pub)
    n_calls = len(caller.calls)
    spath = os.path.join(store, "state.json")
    before = open(spath, "rb").read()
    publisher.reconcile(store, cfg, FakePublisher(fail_times=999))
    publisher.reconcile(store, cfg, FakePublisher())
    assert len(caller.calls) == n_calls              # retries never trade
    assert open(spath, "rb").read() == before
    assert publisher.read_log(store)[f"{T0}:committed"]["status"] == "PUBLISHED"


# ---- 5. static-site integrity at publication time ----

def test_drifted_site_fails_publication(cfg, tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    for d in ("engine", "scripts", "prompts", "schemas", "config", "docs"):
        shutil.copytree(os.path.join(config.ROOT, d), tree / d)
    monkeypatch.setattr(config, "ROOT", str(tree))
    store = tempfile.mkdtemp(prefix="arena-r11-site-")
    eng, site = digests()
    pilot.provision(store, eng, site, T0, total=1)
    caller = ScriptedCaller({})
    victim = tree / "docs/index.html"
    victim.write_bytes(victim.read_bytes() + b"<!--drift-->")
    with pytest.raises(publisher.PublicationError):
        run(store, cfg, caller, FakePublisher())     # READY gate: site check
    assert caller.calls == []
    assert "site-file hash mismatch" in \
        publisher.read_log(store)["ready"]["reason"]


# ---- 6. front-end payload contract (Node harness, fully offline) ----

NODE = shutil.which("node")
pytestmark_node = pytest.mark.skipif(NODE is None, reason="node unavailable")


def _harness(*args):
    out = subprocess.run(
        [NODE, os.path.join(os.path.dirname(__file__), "frontend_harness.js"),
         os.path.join(config.ROOT, "docs", "index.html"), *args],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytestmark_node
def test_production_payload_renders_without_js_errors(cfg, tmp_path):
    store = provisioned_pilot(n=2)
    p = _sol_price()
    caller = ScriptedCaller({"sol_haiku_raw": [long_decision(float(p), 2000)]})
    pub = FakePublisher()
    run(store, cfg, caller, pub)
    committed = [t for t in pub.published
                 if parse_payload(t)["round_lifecycle"] == "ROUND_COMMITTED"]
    a, b = tmp_path / "a.js", tmp_path / "b.js"
    a.write_text(committed[0])
    b.write_text(committed[1])
    res = _harness(str(a), str(b))
    assert res["ok"] is True, res
    assert res["mode"] == "PILOT_12H"
    assert res["boot_live_id"] == parse_payload(committed[0])["publication_id"]
    # an already-open browser receives the NEXT published state automatically
    assert res["polled_live_id"] == parse_payload(committed[1])["publication_id"]


@pytestmark_node
def test_page_falls_back_to_prestart_without_live_payload(cfg):
    res = _harness("none")
    assert res["ok"] is True, res
    assert res["boot_live_id"] is None
    assert res["mode"] == "PREPARATION"


@pytestmark_node
def test_thinking_payload_renders_without_js_errors(cfg, tmp_path):
    store = provisioned_pilot(n=1)
    pub = FakePublisher()
    run(store, cfg, ScriptedCaller({}), pub)
    think = [t for t in pub.published
             if parse_payload(t)["round_lifecycle"] == "THINKING"][0]
    f = tmp_path / "think.js"
    f.write_text(think)
    res = _harness(str(f))
    assert res["ok"] is True, res
