"""DOM contract tests for the build-batch UI (owner wishlist #1-#11) and the
official-mode payload rendering — the production page's real scripts under
the Node harness, offline."""
import json
import os
import shutil
import subprocess

import pytest

from conftest import T0, ScriptedCaller, long_decision
from engine import config, official
from test_official import direct, provisioned_official, run_official
from test_ruling010 import PilotClock, parse_payload
from test_ruling011 import _sol_price

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node unavailable")
MARKER = "OFFICIAL-DOM-THESIS-MARKER range low held on the hourly"


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
def official_payload(tmp_path_factory):
    """A genuine official-run ROUND_COMMITTED payload with one open SOL long."""
    cfg = config.load_config()
    store = provisioned_official(n=1)
    vc = PilotClock(float(T0) - 300)
    dp, pub_dir = direct(vc)
    d = long_decision(float(_sol_price()), 2000)
    d["thesis"] = MARKER
    run_official(store, cfg, ScriptedCaller({"sol_haiku_raw": d}), dp, vc)
    src = os.path.join(pub_dir, "live_payload.js")
    pl = parse_payload(open(src).read())
    assert pl["mode"] == "OFFICIAL_14D"
    p = tmp_path_factory.mktemp("official") / "official.js"
    p.write_text(open(src).read())
    return str(p)


def test_official_mode_banner_and_status(official_payload):
    res = _harness(official_payload)
    assert res["mode"] == "OFFICIAL_14D"
    assert official.BANNER in res["dom"]["prepbanner"]     # top banner updates
    status = res["dom"]["statusstrip"]
    assert "OFFICIAL 14-DAY EXPERIMENT" in status
    assert "ROUND COMMITTED" in status
    assert "live paper accounts" in status


def test_open_positions_age_invalidation_and_thought_process(official_payload):
    res = _harness(official_payload)
    opens = res["dom"]["panel:OPEN POSITIONS"]
    # wishlist #5: age computed from entry_t (opened this boundary => <1h)
    assert "age</span><b>&lt;1h</b>" in opens or "age</span><b><1h</b>" in opens
    # wishlist #6: the exact submitted condition, not just the status
    assert "NOT TRIGGERED — invalidates if price" in opens
    assert "(1h close)" in opens
    # wishlist #4: expandable full thought process on the position card
    assert "click for full thought process" in opens
    assert MARKER in opens
    # a real mark renders real numbers (null-fabrication guards live in
    # test_ruling012's null-mark DOM tests, unchanged)
    assert "MARK N/A" not in opens


def test_comparison_table_raw_vs_ta(official_payload):
    res = _harness(official_payload)
    cmp_html = res["dom"]["comparison"]
    assert "RAW vs TA" in cmp_html                        # wishlist #10
    assert "ONGOING LONG" in cmp_html                     # ongoing trades
    assert "entry" in cmp_html and "stop" in cmp_html and "target" in cmp_html
    assert MARKER[:60] in cmp_html                        # reasoning snippet
    assert "no trades yet" in cmp_html                    # untraded arms honest


def test_ta_language_everywhere_no_feature_label(official_payload):
    res = _harness(official_payload)
    blob = json.dumps(res["dom"])
    # wishlist #3: display language is TA; the word "Feature" never renders
    assert "Feature" not in blob and "FEATURE" not in blob and "Feat " not in blob
    lb = res["dom"]["leaderboard"]
    assert ">TA<" in lb and ">Raw<" in lb                 # arm column renamed
    # the static filter options (not rendered by the DOM stub) are TA too
    src = open(os.path.join(config.ROOT, "docs", "index.html")).read()
    assert "Raw + TA" in src and ">Feature<" not in src


def test_demo_button_gone_but_demo_url_still_labeled(official_payload):
    res = _harness(official_payload)
    assert "View demo scenario" not in json.dumps(res["dom"])   # wishlist #8
    res_demo = _harness("none", "-", "?demo=1")
    assert "DEMONSTRATION" in res_demo["dom"]["panel:MODEL CHAT"]


def test_ticker_compact_and_disclaimer_footnote(official_payload):
    res = _harness(official_payload)
    # wishlist #11: offline harness => compact unavailability line, no big grid
    assert "feed unavailable" in res["dom"]["strip"]
    assert "cell mkt" not in res["dom"]["strip"]
    assert "display-only" in res["dom"]["foot"].lower()
