"""Ruling 012: live Model Chat / Pair Details rendering (asserted on the
rendered DOM text), the THINKING publication hard gate, and the collection
deadline anchored at the SCHEDULED boundary T — all offline, deterministic."""
import json
import os
import shutil
import subprocess

import pytest

from conftest import T0, ScriptedCaller, long_decision
from engine import config, persistence, pilot, publisher, recovery
from test_ruling008 import DeadlineCaller
from test_ruling009 import fresh_store
from test_ruling010 import (FakePublisher, PilotClock, fake_fetch,
                            parse_payload, provisioned_pilot, run)
from test_ruling011 import _sol_price

COINS = ("BTC", "ETH", "SOL")
DEADLINE_S = 720                                     # frozen: T + 12 minutes


# ---- 2. THINKING publication is a hard pre-model gate ----

def test_thinking_failure_blocks_all_model_calls(cfg):
    """READY succeeds, THINKING fails => zero caller invocations, byte-
    identical state, no ledger, schedule progress unchanged, FAILED status."""
    store = provisioned_pilot(n=1)
    caller = ScriptedCaller({})
    pub = FakePublisher(fail_lifecycles={"THINKING"})
    spath = os.path.join(store, "state.json")
    lpath = os.path.join(store, "ledger.jsonl")
    state_before = open(spath, "rb").read()
    with pytest.raises(publisher.PublicationError):
        run(store, cfg, caller, pub)
    assert caller.calls == []                        # zero model calls
    assert open(spath, "rb").read() == state_before  # zero account mutation
    assert not os.path.exists(lpath)                 # zero trading records
    assert pilot.load_schedule(store)["completed"] == []
    log = publisher.read_log(store)
    assert log["ready"]["status"] == "PUBLISHED"     # READY did succeed
    assert log[f"{T0}:thinking"]["status"] == "FAILED"


def test_thinking_retry_then_same_boundary_executes_exactly_once(cfg):
    store = provisioned_pilot(n=1)
    caller = ScriptedCaller({})
    with pytest.raises(publisher.PublicationError):
        run(store, cfg, caller, FakePublisher(fail_lifecycles={"THINKING"}))
    assert caller.calls == []
    sched0 = pilot.load_schedule(store)
    pub = FakePublisher()
    sched = run(store, cfg, caller, pub)             # restart, same schedule
    assert sched["boundaries"] == sched0["boundaries"]   # no new boundary
    assert sched["completed"] == [T0]
    assert len(caller.calls) == 18                   # the boundary ran ONCE
    log = publisher.read_log(store)
    assert log[f"{T0}:thinking"]["status"] == "PUBLISHED"   # same publication
    assert len(pub.by_lifecycle("ROUND_COMMITTED")) == 1


def test_thinking_gate_holds_on_second_boundary_too(cfg):
    """Boundary 1 fully publishes; boundary 2's THINKING fails => run stops
    with exactly boundary 1 executed and no boundary-2 model calls."""
    store = provisioned_pilot(n=2)
    caller = ScriptedCaller({})
    pub = FakePublisher(ok_first=3, fail_lifecycles={"THINKING"})
    with pytest.raises(publisher.PublicationError):
        run(store, cfg, caller, pub)
    assert len(caller.calls) == 18                   # boundary 1 only
    assert pilot.load_schedule(store)["completed"] == [T0]
    log = publisher.read_log(store)
    assert log[f"{T0 + 3600}:thinking"]["status"] == "FAILED"


# ---- 3. deadline anchored at scheduled boundary T ----

class CostedPublisher(FakePublisher):
    """Advances the shared pilot clock: READY costs `ready_cost` seconds,
    each THINKING publication costs `think_cost` (publication + public
    verification time). Deterministic: called from the single pilot thread."""

    def __init__(self, vc, ready_cost=60.0, think_cost=0.0):
        super().__init__()
        self.vc, self.ready_cost, self.think_cost = vc, ready_cost, think_cost

    def __call__(self, text):
        lc = parse_payload(text).get("round_lifecycle")
        self.vc.t += self.ready_cost if lc == "READY" else \
            (self.think_cost if lc == "THINKING" else 0.0)
        return super().__call__(text)


def costed_fetch(vc, per_coin_cost):
    def fetch(coin, T, first):
        vc.t += per_coin_cost                        # market retrieval time
        return fake_fetch(coin, T, first)
    return fetch


def run_costed(cfg, think_cost, per_coin_cost, grace=120):
    store = provisioned_pilot(n=1)
    vc = PilotClock(float(T0) - 600)                 # READY happens pre-T
    caller = ScriptedCaller({})
    pub = CostedPublisher(vc, think_cost=think_cost)
    sched = pilot.run_pilot(store, cfg, caller, costed_fetch(vc, per_coin_cost),
                            pub, clock=vc, sleep=vc.sleep, grace=grace)
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    pairs = [e for e in ledger if e.get("status")]
    return sched, caller, pairs, vc


def test_budget_shared_by_grace_thinking_and_fetch_still_fits(cfg):
    """grace 2min + THINKING 3min + fetch 1.5min => coordinator enters at
    T+390 with 330s left; a 120s request fits => all pairs commit."""
    sched, caller, pairs, vc = run_costed(cfg, think_cost=180.0,
                                          per_coin_cost=30.0)
    assert vc.t - T0 == pytest.approx(390.0)         # entered at T+6:30
    assert len(caller.calls) == 18
    assert {e["status"] for e in pairs} == {"PAIR_COMMITTED"}
    assert sched["completed"] == [T0]


def test_pre_request_delays_consume_budget_no_extension(cfg):
    """grace 2min + THINKING 6:40 + fetch 1:30 => T+610; a 120s request no
    longer fits before T+720 => ZERO model calls, pairs abort — the deadline
    was NOT re-anchored at coordinator entry (old behavior would commit)."""
    sched, caller, pairs, _ = run_costed(cfg, think_cost=400.0,
                                         per_coin_cost=30.0)
    assert caller.calls == []                        # no request may begin
    assert {e["status"] for e in pairs} == {"PAIR_ABORTED"}
    assert {e["reason"] for e in pairs} == {"deadline_exceeded"}
    assert sched["completed"] == [T0]                # boundary closed, aborted


def test_budget_fully_consumed_before_entry_aborts_all(cfg):
    """THINKING + fetch walk the clock to exactly T+720: every pair aborts
    deadline_exceeded with zero calls and no extension."""
    sched, caller, pairs, vc = run_costed(cfg, think_cost=480.0,
                                          per_coin_cost=40.0)
    assert vc.t - T0 == pytest.approx(720.0)
    assert caller.calls == []
    assert {e["status"] for e in pairs} == {"PAIR_ABORTED"}
    assert {e["reason"] for e in pairs} == {"deadline_exceeded"}


@pytest.mark.parametrize("rep", range(3))
@pytest.mark.parametrize("offset,ok", [(719.0, True), (720.0, False),
                                       (721.0, False)])
def test_absolute_deadline_exact_boundary(cfg, snapshots, offset, ok, rep):
    """Coordinator entered 5 minutes late; results completing at exactly
    T+11:59 execute, at exactly T+12:00 and T+12:01 never execute. Under the
    old entry-anchored deadline (entry+720 = T+1020) every case would commit,
    so this proves the anchor is the SCHEDULED boundary. Repeated 3x each and
    rendezvous-synchronized — deterministic."""
    store = fresh_store()
    clockbox = [T0 + 300.0]                          # late entry: T+5:00
    caller = DeadlineCaller(clockbox, {"btc_haiku_raw": T0 + offset,
                                       "btc_haiku_ta": T0 + offset},
                            rendezvous=True)
    ledger, *_ = recovery.run_checkpointed(T0, snapshots, caller, cfg, store,
                                           clock=lambda: clockbox[0],
                                           deadline=T0 + float(DEADLINE_S))
    pair = [e for e in ledger if e.get("pair") == "btc_haiku"][0]
    if ok:
        assert pair["status"] == "PAIR_COMMITTED"
    else:
        assert pair["status"] == "PAIR_ABORTED"
        assert pair["reason"] == "deadline_exceeded"


def test_restart_does_not_reset_deadline(cfg):
    """Crash mid-boundary; the restart resumes the SAME boundary with the
    SAME T-anchored deadline already expired => recovery aborts stand and no
    model is re-asked with fresh budget."""
    store = provisioned_pilot(n=1)
    caller = ScriptedCaller({})
    pub = FakePublisher()
    vc = PilotClock()
    with pytest.raises(recovery.CrashError):
        pilot.run_pilot(store, cfg, caller, fake_fetch, pub,
                        clock=vc, sleep=vc.sleep, crash_at="after_prompts")
    n_calls = len(caller.calls)
    vc2 = PilotClock(float(T0) + 2000)               # restart AFTER T+12min
    sched = pilot.run_pilot(store, cfg, caller, fake_fetch, pub,
                            clock=vc2, sleep=vc2.sleep)
    assert len(caller.calls) == n_calls              # no fresh budget granted
    assert sched["completed"] == [T0]
    ledger = persistence.read_ledger(os.path.join(store, "ledger.jsonl"))
    assert {e["reason"] for e in ledger if e.get("status")} == {"crash_recovery"}


# ---- 1. front-end: rendered DOM text per mode (Node harness) ----

NODE = shutil.which("node")
pytestmark_node = pytest.mark.skipif(NODE is None, reason="node unavailable")
MARKER = "Distinct thesis marker XYZ123 for DOM assertion"


def _harness(payload_a, payload_b="-", query=""):
    out = subprocess.run(
        [NODE, os.path.join(os.path.dirname(__file__), "frontend_harness.js"),
         os.path.join(config.ROOT, "docs", "index.html"),
         payload_a, payload_b, query],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["ok"] is True, res
    return res


@pytest.fixture(scope="module")
def pilot_payloads(cfg, tmp_path_factory):
    """One real 1-boundary pilot with a distinctive live thesis; returns
    file paths of the exact THINKING and ROUND_COMMITTED payloads."""
    store = provisioned_pilot(n=1)
    d = long_decision(float(_sol_price()), 2000)
    d["thesis"] = MARKER
    caller = ScriptedCaller({"sol_haiku_raw": [d]})
    pub = FakePublisher()
    run(store, cfg, caller, pub)
    tmp = tmp_path_factory.mktemp("payloads")
    paths = {}
    for lc, name in (("THINKING", "think"), ("ROUND_COMMITTED", "commit")):
        text = [t for t in pub.published
                if parse_payload(t)["round_lifecycle"] == lc][0]
        p = tmp / f"{name}.js"
        p.write_text(text)
        paths[lc] = str(p)
    return paths


@pytestmark_node
def test_dom_thinking_renders_live_status_not_prestart(cfg, pilot_payloads):
    res = _harness(pilot_payloads["THINKING"])
    chat = res["dom"]["panel:MODEL CHAT"]
    assert "THINKING / AWAITING RESPONSE" in chat
    assert "pilot has not started" not in chat
    assert "DECISION RECEIVED" not in chat           # nothing invented
    assert "t=demo" not in chat and "(demo)" not in chat
    # display language is Raw/TA (wishlist #3); internal ids stay raw/ta
    for label in ("Haiku 4.5", "Sonnet 5", "Opus 4.8", "BTC", "SOL",
                  "Raw", "TA"):
        assert label in chat
    assert "processing the current hourly round" in res["dom"]["notice"]
    pairs = res["dom"]["panel:PAIR DETAILS"]
    assert "THINKING / AWAITING RESPONSE" in pairs


@pytestmark_node
def test_dom_committed_renders_actual_thesis_and_pair_status(cfg,
                                                            pilot_payloads):
    res = _harness(pilot_payloads["ROUND_COMMITTED"])
    chat = res["dom"]["panel:MODEL CHAT"]
    assert MARKER in chat                            # the ACTUAL thesis
    assert "DECISION RECEIVED" in chat
    assert "pilot has not started" not in chat
    assert "t=demo" not in chat and "(demo)" not in chat
    assert "first attempt valid" not in chat
    assert "PAIR_COMMITTED" in chat                  # genuine ledger status
    pairs = res["dom"]["panel:PAIR DETAILS"]
    assert "Awaiting first paired decision" not in pairs
    assert "last round: PAIR_COMMITTED" in pairs
    assert "direction" in pairs                      # AGREE/DISAGREE computed
    assert "experiment is complete" in res["dom"]["notice"]
    opens = res["dom"]["panel:OPEN POSITIONS"]
    assert "LONG" in opens                           # the live SOL position


@pytestmark_node
def test_dom_thinking_to_committed_updates_open_browser(cfg, pilot_payloads):
    res = _harness(pilot_payloads["THINKING"], pilot_payloads["ROUND_COMMITTED"])
    assert "THINKING / AWAITING RESPONSE" in res["dom"]["panel:MODEL CHAT"]
    chat2 = res["dom2"]["panel:MODEL CHAT"]
    assert MARKER in chat2 and "DECISION RECEIVED" in chat2
    assert res["polled_live_id"] != res["boot_live_id"]


@pytestmark_node
def test_dom_preparation_mode_still_waits(cfg):
    res = _harness("none")
    chat = res["dom"]["panel:MODEL CHAT"]
    assert "WAITING" in chat
    assert "pilot has not started" in chat
    assert "THINKING / AWAITING RESPONSE" not in chat


# ---- Ruling 013: null market values must never render as zero ----

@pytest.fixture(scope="module")
def null_mark_payload(cfg, tmp_path_factory):
    """Genuine live payload: SOL long opened at boundary 1, SOL data
    unavailable at boundary 2 => open position with mark_unavailable=true and
    null mark/equity/unrealized_pnl/notional; BTC/ETH accounts stay validly
    marked."""
    store = provisioned_pilot(n=2)
    d = long_decision(float(_sol_price()), 2000)
    d["thesis"] = MARKER
    caller = ScriptedCaller({"sol_haiku_raw": [d]})

    def flaky(coin, T, first):
        if coin == "SOL" and T > T0:
            raise RuntimeError("sol feed down")
        return fake_fetch(coin, T, first)
    pub = FakePublisher()
    vc = PilotClock()
    pilot.run_pilot(store, cfg, caller, flaky, pub, clock=vc, sleep=vc.sleep)
    text = [t for t in pub.published
            if parse_payload(t)["round_lifecycle"] == "ROUND_COMMITTED"][-1]
    pl = parse_payload(text)
    row = [a for a in pl["coins"]["SOL"]["accounts"]
           if a["id"] == "sol_haiku_raw"][0]
    assert row["mark_unavailable"] is True and row["equity"] is None \
        and row["unrealized_pnl"] is None and row["notional"] is None
    p = tmp_path_factory.mktemp("nullmark") / "null.js"
    p.write_text(text)
    return str(p)


@pytestmark_node
def test_dom_null_mark_open_positions_never_zero(cfg, null_mark_payload):
    res = _harness(null_mark_payload)
    opens = res["dom"]["panel:OPEN POSITIONS"]
    # UI-rev wording: explicit unavailability, never a fabricated number
    assert "price unavailable" in opens              # explicit unavailability
    assert "CURRENT PRICE</span><b>unavailable" in opens   # a.mark used
    assert "CURRENT PRICE</span><b>$0" not in opens  # never a zero mark
    assert "+$0.00" not in opens                     # never false zero P&L
    assert "-$0.00" not in opens
    assert "LONG" in opens                           # position stays visible


@pytestmark_node
def test_dom_null_mark_pair_details_no_fabricated_gaps(cfg,
                                                      null_mark_payload):
    res = _harness(null_mark_payload)
    pairs = res["dom"]["panel:PAIR DETAILS"]
    # the mentor-probed fabrication: null equity treated as 0 => -$10,000.00
    assert "-$10,000.00" not in pairs
    # the null pair's gaps are explicitly unavailable, never computed
    assert "equity gap</span><b>MARK N/A</b>" in pairs
    assert "size gap</span><b>MARK N/A</b>" in pairs
    # validly marked pairs still compute genuine gaps (flat pairs: $0.00)
    assert "equity gap</span><b>$0.00</b>" in pairs


@pytestmark_node
def test_dom_null_mark_leaderboard_unranked_but_visible(cfg,
                                                       null_mark_payload):
    res = _harness(null_mark_payload)
    lb = res["dom"]["leaderboard"]
    assert "MARK N/A" in lb                          # return not shown as 0%
    assert "MARK UNAVAILABLE" in lb                  # clear visible status
    assert "+0.00%" in lb                            # valid rows still normal
    assert "$10,000.00" in lb or "$9,99" in lb       # valid equities rendered
    chat = res["dom"]["panel:MODEL CHAT"]
    assert MARKER in chat                            # account remains visible


@pytestmark_node
def test_dom_valid_marks_still_render_normally(cfg, pilot_payloads):
    """Regression guard: a fully marked committed payload shows real money
    values and no MARK N/A anywhere in the open-positions panel."""
    res = _harness(pilot_payloads["ROUND_COMMITTED"])
    opens = res["dom"]["panel:OPEN POSITIONS"]
    assert "MARK N/A" not in opens and "unavailable" not in opens
    assert "CURRENT PRICE" in opens and "LONG" in opens
    assert "MARK N/A" not in res["dom"]["leaderboard"]


@pytestmark_node
def test_dom_demo_mode_clearly_labeled(cfg):
    res = _harness("none", "-", "?demo=1")
    chat = res["dom"]["panel:MODEL CHAT"]
    assert "DEMONSTRATION" in chat
    assert "[DEMO]" in chat                          # demo theses labeled
    assert "demo scenario" in res["dom"]["leaderboard"] \
        or "demonstration scenario" in res["dom"]["leaderboard"]
