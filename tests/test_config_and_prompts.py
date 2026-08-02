"""Config integrity, canonical validation, prompt rendering, arm equality."""
import json
import re
from decimal import Decimal

import pytest

from conftest import T0, flat_decision
from engine import config, state, prompts, features

PROHIBITED = ["bullish", "bearish", "strong", "weak", "overbought", "oversold",
              "confirmation", "divergence", "breakout", "favorable", "unfavorable",
              "stretched", "extended"]


def test_json_duplicate_keys_rejected(tmp_path):
    p = tmp_path / "dup.json"
    p.write_text('{"a": 1, "a": 2}')
    with pytest.raises(ValueError):
        json.load(open(p), object_pairs_hook=config._reject_dupes)


def test_canonical_files_parse(cfg):
    assert cfg["experiment"] == "v1.3"
    assert config.load_schema()["input_schema"]["additionalProperties"] is False


def test_manifest_verifies_and_detects_change(tmp_path, monkeypatch):
    m = config.build_manifest()
    assert config.verify_integrity(m)
    bad = {"files": dict(m["files"]), "combined": "0" * 64}
    with pytest.raises(config.IntegrityError):
        config.verify_integrity(bad)


def test_param_change_changes_config_hash(cfg):
    h1 = config.file_hash("config/v1/experiment.json")
    text = config.load_text("config/v1/experiment.json")
    import hashlib
    h2 = hashlib.sha256(text.replace('"SMA20_H"', '"SMA21_H"').encode()).hexdigest()
    assert h1 != h2


def test_schema_has_no_intended_horizon():
    assert "intended_horizon" not in config.load_text("schemas/v1/decision.schema.json")


def test_prompt_rendering_deterministic(accounts, snapshots, cfg):
    a = accounts["btc_haiku_raw"]
    s1, u1 = prompts.render(a, snapshots["BTC"], cfg)
    s2, u2 = prompts.render(a, snapshots["BTC"], cfg)
    assert (s1, u1) == (s2, u2)
    assert "This account trades only BTC" in u1
    assert "synthetic BTC/USD perpetual paper contract" in u1


def test_raw_and_feature_differ_only_by_feature_block(accounts, snapshots, cfg):
    raw = prompts.render(accounts["eth_opus_raw"], snapshots["ETH"], cfg)[1]
    feat = prompts.render(accounts["eth_opus_ta"], snapshots["ETH"], cfg)[1]
    block = re.search(r"=== FEATURE SUMMARY ===\n.*?%\n\n", feat, re.S)
    assert block, "feature block missing"
    assert feat.replace(block.group(0), "") == raw


def test_raw_prompt_has_no_derived_features(accounts, snapshots, cfg):
    raw = prompts.render(accounts["sol_sonnet_raw"], snapshots["SOL"], cfg)[1]
    for term in ("RSI", "SMA", "ATR", "VWAP", "FEATURE SUMMARY", "24h change",
                 "MACD", "Bollinger", "ratio"):
        assert term not in raw


def test_removed_indicators_absent_from_feature_prompt(accounts, snapshots, cfg):
    feat = prompts.render(accounts["sol_sonnet_ta"], snapshots["SOL"], cfg)[1]
    for term in ("MACD", "Bollinger", "MFI", "Daily SMA", "Daily RSI",
                 "Fear", "sentiment"):
        assert term not in feat


def test_no_prohibited_interpretation_words(accounts, snapshots, cfg):
    for aid in ("btc_haiku_raw", "btc_haiku_ta"):
        sys_p, user = prompts.render(accounts[aid], snapshots["BTC"], cfg)
        low = user.lower()
        for w in PROHIBITED:
            assert not re.search(rf"\b{w}\b", low), (aid, w)


def test_volume_disclosure_identical_in_both_arms(accounts, snapshots, cfg):
    line = ("Volume is base-asset trading volume reported by the Kraken spot "
            "market for each candle. It is not global crypto-market volume or "
            "perpetual-futures volume.")
    for aid in ("btc_opus_raw", "btc_opus_ta"):
        assert prompts.render(accounts[aid], snapshots["BTC"], cfg)[1].count(line) == 1


def test_model_freedom_and_hold_permission_in_system(accounts, snapshots, cfg):
    sys_p, _ = prompts.render(accounts["btc_haiku_raw"], snapshots["BTC"], cfg)
    assert "may hold a position across multiple decision rounds" in sys_p
    assert "You may use, combine, or ignore any supplied information" in sys_p
    assert not re.search(r"(?i)primary (analytical )?timeframe|mandatory timeframe", sys_p)


def test_unrendered_placeholder_raises(accounts, snapshots, cfg, monkeypatch):
    monkeypatch.setattr(prompts.config, "load_text",
                        lambda p: "{NOT_A_REAL_PLACEHOLDER}" if "user_raw" in p
                        else config.load_text(p))
    with pytest.raises(ValueError):
        prompts.render(accounts["btc_haiku_raw"], snapshots["BTC"], cfg)


def test_feature_block_contains_only_approved_outputs(accounts, snapshots, cfg):
    feat = prompts.render(accounts["btc_haiku_ta"], snapshots["BTC"], cfg)[1]
    block = re.search(r"=== FEATURE SUMMARY ===\n(.*?)\n\n", feat, re.S).group(1)
    labels = [l.split(":")[0] for l in block.split("\n")[1:]]
    assert labels == ["RSI(14), hourly", "SMA(20), hourly", "SMA(50), hourly",
                     "ATR(14), hourly", "Latest completed 1h volume",
                     "Mean volume of previous 24 completed 1h candles",
                     "Latest volume / previous-24h mean", "Rolling VWAP(24h)",
                     "Price minus VWAP"]
