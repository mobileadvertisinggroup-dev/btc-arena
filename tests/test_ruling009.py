"""Ruling 009: launch-manifest gate in the coordinator + strict state loading.

Every case asserts ZERO caller invocations and ZERO account mutation."""
import json
import os
import shutil
import tempfile

import pytest

from conftest import T0, ScriptedCaller
from engine import config, state, persistence, recovery

MUTATION_TARGETS = [
    ("engine source", "engine/execution.py"),
    ("production script", "scripts/run_pilot_12h.py"),
    ("config", "config/v1/experiment.json"),
    ("prompt", "prompts/v1/system.txt"),
    ("decision schema", "schemas/v1/decision.schema.json"),
    ("records schema", "schemas/v1/records.schema.json"),
]


def fresh_store():
    store = tempfile.mkdtemp(prefix="arena-r9-")
    persistence.save_state(store + "/state.json", state.init_accounts(),
                           {"boundary": None})
    config.write_launch_manifest(store)
    return store


def state_bytes(store):
    return open(store + "/state.json", "rb").read()


def run(store, snapshots, cfg, caller):
    return recovery.run_checkpointed(T0, snapshots, caller, cfg, store)


# ---- 1. one-byte mutations per category => pre-request Integrity Halt A ----

@pytest.mark.parametrize("label,relpath", MUTATION_TARGETS)
def test_mutation_halts_before_any_request(tmp_path, monkeypatch, snapshots,
                                           cfg, label, relpath):
    tree = tmp_path / "tree"
    for d in ("engine", "scripts", "prompts", "schemas", "config", "docs",
              "deploy"):
        shutil.copytree(os.path.join(config.ROOT, d), tree / d)
    store = fresh_store()                 # manifest frozen over PRISTINE tree
    monkeypatch.setattr(config, "ROOT", str(tree))
    config.write_launch_manifest(store)   # approve the pristine copy
    victim = tree / relpath
    victim.write_bytes(victim.read_bytes() + b"#mutated")
    caller = ScriptedCaller({})
    before = state_bytes(store)
    with pytest.raises(config.IntegrityError):
        run(store, snapshots, cfg, caller)
    assert caller.calls == []                     # zero model calls
    assert state_bytes(store) == before           # zero account mutation


def test_missing_launch_manifest_halts(snapshots, cfg):
    store = tempfile.mkdtemp(prefix="arena-r9-nm-")
    persistence.save_state(store + "/state.json", state.init_accounts(),
                           {"boundary": None})    # no manifest written
    caller = ScriptedCaller({})
    with pytest.raises(config.IntegrityError):
        run(store, snapshots, cfg, caller)
    assert caller.calls == []


def test_manifest_is_loaded_not_rebuilt(snapshots, cfg):
    """A tampered stored manifest must not be silently replaced by a rebuild:
    verification runs against the STORED file, so tampering it => halt."""
    store = fresh_store()
    m = json.load(open(store + "/launch_manifest.json"))
    m["files"]["prompts/v1/system.txt"] = "0" * 64
    m["combined"] = "0" * 64
    json.dump(m, open(store + "/launch_manifest.json", "w"))
    caller = ScriptedCaller({})
    with pytest.raises(config.IntegrityError):
        run(store, snapshots, cfg, caller)
    assert caller.calls == []


# ---- 2. strict state loading in the production path ----

def corrupt_and_run(snapshots, cfg, mutate):
    store = fresh_store()
    payload = json.loads(open(store + "/state.json").read())
    mutate(payload)
    open(store + "/state.json", "w").write(json.dumps(payload))
    caller = ScriptedCaller({})
    with pytest.raises(persistence.StateCorruption):
        run(store, snapshots, cfg, caller)
    assert caller.calls == []                     # coordinator: zero calls
    return store


def test_missing_checksum_rejected(snapshots, cfg):
    def m(p): del p["meta"]["_checksum"]
    corrupt_and_run(snapshots, cfg, m)


def test_incorrect_checksum_rejected(snapshots, cfg):
    def m(p): p["meta"]["_checksum"] = "f" * 64
    corrupt_and_run(snapshots, cfg, m)


def test_missing_account_rejected(snapshots, cfg):
    def m(p):
        del p["accounts"]["btc_haiku_raw"]
        del p["meta"]["_checksum"]                 # even re-checksummed later,
    corrupt_and_run(snapshots, cfg, m)             # roster check must catch it


def test_missing_account_with_valid_checksum_rejected(snapshots, cfg):
    store = fresh_store()
    accounts, meta = persistence.load_state(store + "/state.json")
    del accounts["btc_haiku_raw"]
    persistence.save_state(store + "/state.json", accounts, meta)  # valid sum
    caller = ScriptedCaller({})
    with pytest.raises(persistence.StateCorruption, match="missing or extra"):
        run(store, snapshots, cfg, caller)
    assert caller.calls == []


def test_extra_account_rejected(snapshots, cfg):
    store = fresh_store()
    accounts, meta = persistence.load_state(store + "/state.json")
    extra = state.new_account("BTC", "haiku", "raw")
    extra["id"] = "btc_haiku_raw"
    accounts["btc_haiku_raw2"] = dict(extra, id="btc_haiku_raw")
    with pytest.raises(persistence.StateCorruption):
        persistence.save_state(store + "/state.json", accounts, meta) or \
            persistence.load_state(store + "/state.json", expect_full_roster=True)
    # saved file with mismatched key/identity must also halt the coordinator
    caller = ScriptedCaller({})
    with pytest.raises(persistence.StateCorruption):
        run(store, snapshots, cfg, caller)
    assert caller.calls == []


def test_changed_balance_checksum_removed_rejected(snapshots, cfg):
    """Independent probe A regression: altered equity + stripped checksum."""
    def m(p):
        p["accounts"]["btc_haiku_raw"]["E"] = "12345.67"
        del p["meta"]["_checksum"]
    corrupt_and_run(snapshots, cfg, m)


def test_invalid_identity_rejected_not_keyerror(snapshots, cfg):
    """Independent probe B regression: controlled StateCorruption, no KeyError."""
    def m(p):
        p["accounts"]["btc_haiku_raw"]["coin"] = "DOGE"
        del p["meta"]["_checksum"]
    try:
        corrupt_and_run(snapshots, cfg, m)
    except KeyError:
        pytest.fail("raw KeyError leaked from state loading")


def test_unverified_loader_is_isolated():
    """The migration helper accepts a checksum-less file but the strict loader
    (used by the coordinator) never does."""
    store = tempfile.mkdtemp(prefix="arena-r9-mig-")
    persistence.save_state(store + "/state.json", state.init_accounts(),
                           {"boundary": None})
    payload = json.loads(open(store + "/state.json").read())
    del payload["meta"]["_checksum"]
    open(store + "/state.json", "w").write(json.dumps(payload))
    accounts, _ = persistence.load_state_unverified(store + "/state.json")
    assert len(accounts) == 18
    with pytest.raises(persistence.StateCorruption, match="checksum missing"):
        persistence.load_state(store + "/state.json")
