"""Prompt golden files (005.10) and record-schema validation (005.11).

Regenerate goldens:  python3 tests/test_goldens_and_schemas.py regen
"""
import json
import os
import re
import sys
from decimal import Decimal

import jsonschema

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conftest import T0, ScriptedCaller, long_decision  # noqa: E402
from engine import config, state, prompts, execution, replay, rounds  # noqa: E402

GOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goldens")
SCENARIOS = ("first_flat", "mature_open", "triggered", "five_trades")
PROHIBITED = ["bullish", "bearish", "overbought", "oversold", "confirmation",
              "divergence", "breakout", "favorable", "stretched"]
DISCLOSURE = ("Volume is base-asset trading volume reported by the Kraken spot "
              "market for each candle. It is not global crypto-market volume or "
              "perpetual-futures volume.")


def build(scenario, coin, arm, p):
    a = state.new_account(coin, "haiku", arm)
    if scenario == "first_flat":
        return a
    if scenario in ("mature_open", "triggered"):
        d = long_decision(p, 4000)
        d["thesis"] = "golden mature thesis"
        execution.apply_decision(a, d, p, T0 - 7200)
        a["theses"] = [{"t": "2025-08-10T22:00:00Z", "text": "golden mature thesis"}]
        if scenario == "triggered":
            from engine import lifecycle
            lifecycle.latch(a["lifecycle"], T0 - 3600, T0 - 3600)
        return a
    for i in range(5):                       # five_trades
        d = long_decision(p, 1000 + i)
        execution.apply_decision(a, d, p, T0 - 9000 + i * 600)
        f = {"position": "flat", "size_usd": 0, "stop_loss": None,
             "take_profit": None, "thesis": "x", "invalidation": None,
             "watch_condition": {"timeframe": "1h_close",
                                 "operator": "price_at_or_above",
                                 "level": float(p * Decimal("1.1"))}}
        execution.apply_decision(a, f, p, T0 - 8700 + i * 600)
    return a


def golden_path(scenario, coin, arm):
    return os.path.join(GOLD, f"{scenario}_{coin.lower()}_{arm}.txt")


def render_all(snapshots, cfg):
    out = {}
    for sc in SCENARIOS:
        for coin in ("BTC", "ETH", "SOL"):
            for arm in ("raw", "ta"):
                a = build(sc, coin, arm, snapshots[coin]["P_T"])
                out[(sc, coin, arm)] = prompts.render(a, snapshots[coin], cfg)[1]
    return out


def test_goldens_byte_identical_regeneration(snapshots, cfg):
    rendered = render_all(snapshots, cfg)
    assert render_all(snapshots, cfg) == rendered      # deterministic
    missing = []
    for key, text in rendered.items():
        path = golden_path(*key)
        if not os.path.exists(path):
            missing.append(path)
            continue
        assert open(path).read() == text, f"golden drift: {path}"
    assert not missing, f"goldens missing (run regen): {missing[:3]}"


def test_goldens_content_rules(snapshots, cfg):
    rendered = render_all(snapshots, cfg)
    for (sc, coin, arm), text in rendered.items():
        assert text.count(DISCLOSURE) == 1
        low = text.lower()
        for w in PROHIBITED:
            assert not re.search(rf"\b{w}\b", low)
        for other in {"BTC", "ETH", "SOL"} - {coin}:
            assert other not in text                   # no unsupported/foreign data
        assert not re.search(r"\{[A-Z0-9_]+\}", text)  # no undeclared placeholders
        assert ("15-minute" not in text and "4-hour analytical" not in text)
    for sc in SCENARIOS:
        for coin in ("BTC", "ETH", "SOL"):
            raw, ta = rendered[(sc, coin, "raw")], rendered[(sc, coin, "ta")]
            m = re.search(r"=== FEATURE SUMMARY ===\n.*?%\n\n", ta, re.S)
            assert m and ta.replace(m.group(0), "") == raw
    trig = rendered[("triggered", "BTC", "raw")]
    assert "TRIGGERED at" in trig and "This record is permanent" in trig
    five = rendered[("five_trades", "BTC", "raw")]
    assert "5 total" in five and "watch condition" in five.lower()


# ---- record schemas ----

def _defs():
    return config.load_json("schemas/v1/records.schema.json")["$defs"]


def _validate(kind, obj):
    jsonschema.validate(obj, _defs()[kind])


def test_attempt_records_validate(accounts, snapshots, cfg):
    p = snapshots["BTC"]["P_T"]
    bad = dict(long_decision(p), size_usd=-1)
    accounts["eth_opus_raw"]["terminal"] = True
    script = {"btc_haiku_raw": [bad, long_decision(p, 2000)],
              "sol_opus_ta": [rounds.TransportError("x")] * 4}
    from conftest import run_prod
    ledger, archive, _, caller = run_prod(accounts, snapshots, cfg,
                                          ScriptedCaller(script))
    assert archive
    for rec in archive:
        _validate("attempt", rec)
    # accepted-first, rejected+corrected, transport failure all present
    assert any(r["became_executed_decision"] and r["attempt_number"] == 1 for r in archive)
    assert any(r["fixed_rejection_reasons"] for r in archive)
    assert any(r["transport_error_category"] for r in archive)
    assert not any(r["account_id"] == "eth_opus_raw" for r in archive)  # terminal: none
    for e in ledger:
        _validate("pair_ledger", e)
    assert any(e["status"] == "PAIR_ABORTED" and e.get("caused_by_arm") == "ta"
               for e in ledger)


def test_lifecycle_trade_dashboard_schemas(accounts, snapshots, cfg):
    from engine import dashboard, persistence
    p = snapshots["BTC"]["P_T"]
    a = accounts["btc_haiku_raw"]
    execution.apply_decision(a, long_decision(p, 3000), p, T0)
    rec = []
    replay.replay([a], [{"t": T0 + 60, "o": p * Decimal("0.9"),
                         "h": p * Decimal("0.9"), "l": p * Decimal("0.89"),
                         "c": p * Decimal("0.9"), "v": Decimal(1)}], rec)
    enc = persistence._enc(a)
    for lc in enc["lifecycles"]:
        _validate("lifecycle", lc)
    for tr in enc["trades"]:
        _validate("trade", tr)
    from conftest import run_prod
    ledger, *_ = run_prod(accounts, snapshots, cfg, ScriptedCaller({}))
    pl = dashboard.payload(accounts, ledger, snapshots,
                           {"ts": T0, "code_hash": "x"},
                           config.build_manifest(), cfg)
    _validate("dashboard", json.loads(json.dumps(pl, default=str)))


if __name__ == "__main__" and "regen" in sys.argv:
    from engine import marketdata
    from conftest import load_fix
    os.makedirs(GOLD, exist_ok=True)
    cfg = config.load_config()
    snaps = {c: marketdata.build_snapshot(c, load_fix(c, "1m"), load_fix(c, "1h"),
                                          load_fix(c, "1d"), T0)
             for c in ("BTC", "ETH", "SOL")}
    for key, text in render_all(snaps, cfg).items():
        with open(golden_path(*key), "w") as f:
            f.write(text)
    print(f"wrote {len(SCENARIOS) * 6} goldens to {GOLD}")
