import json
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import config, state, marketdata, rounds, recovery, persistence  # noqa: E402
import tempfile

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
T0 = 1754870400  # matches gen_fixtures


def load_fix(coin, tf):
    with open(os.path.join(FIX, f"{coin.lower()}_{tf}.json")) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _network_block(monkeypatch):
    """Hard technical network block for every test (Ruling 005.8)."""
    import socket
    import urllib.request
    import http.client
    import subprocess

    def blocked(*a, **k):
        raise RuntimeError("NETWORK BLOCKED: tests are hermetic")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", blocked)
    real_popen = subprocess.Popen

    def guarded_popen(args, *a, **k):
        cmd = args if isinstance(args, str) else " ".join(map(str, args))
        if any(tok in cmd for tok in ("curl", "wget", "nc ", "ssh", "ping")):
            raise RuntimeError("NETWORK BLOCKED: subprocess network command")
        return real_popen(args, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)


@pytest.fixture(scope="session")
def cfg():
    return config.load_config()


@pytest.fixture()
def accounts():
    return state.init_accounts()


@pytest.fixture(scope="session")
def snapshots():
    return {c: marketdata.build_snapshot(c, load_fix(c, "1m"), load_fix(c, "1h"),
                                         load_fix(c, "1d"), T0)
            for c in ("BTC", "ETH", "SOL")}


def flat_decision():
    return {"position": "flat", "size_usd": 0, "stop_loss": None,
            "take_profit": None, "thesis": "staying flat this round",
            "invalidation": None, "watch_condition": None}


def long_decision(p_t, size=5000):
    p = Decimal(str(p_t))
    return {"position": "long", "size_usd": size,
            "stop_loss": float(p * Decimal("0.97")),
            "take_profit": float(p * Decimal("1.05")),
            "thesis": "test long with RSI reference",
            "invalidation": {"timeframe": "1h_close", "operator": "price_at_or_below",
                             "level": float(p * Decimal("0.96"))},
            "watch_condition": None}


class ScriptedCaller:
    """Offline stub caller: decisions per account id (list = per attempt)."""

    def __init__(self, script, default="flat"):
        import threading
        self.script = dict(script)
        self.default = default
        self.calls = []
        self.in_flight_max = 0
        self._lock = threading.Lock()

    def __call__(self, account_id, system, user, retry_msg):
        with self._lock:
            self.calls.append({"id": account_id, "retry": retry_msg is not None})
            item = self.script.get(account_id)
            if isinstance(item, list):
                item = item.pop(0) if item else None
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(account_id, system, user, retry_msg)
        if item is not None:
            return item
        if self.default == "flat":
            return flat_decision()
        raise rounds.TransportError("no script")


def run_prod(accounts, snapshots, cfg, caller=None, clock=None, candles=None,
             crash_at=None, store=None, replay_spec=None):
    """Run the AUTHORITATIVE coordinator (recovery.run_checkpointed) against a
    throwaway store, then mirror the persisted result back into `accounts`."""
    if not callable(caller):
        caller = ScriptedCaller(caller or {})
    store = store or tempfile.mkdtemp(prefix="arena-prod-")
    persistence.save_state(store + "/state.json", accounts, {"boundary": None})
    config.write_launch_manifest(store)
    if replay_spec is None and candles is not None:
        replay_spec = {coin: {"start": cs[0]["t"] if cs else T0,
                              "end": (cs[-1]["t"] + 60) if cs else T0,
                              "candles": cs} for coin, cs in candles.items()}
    ledger, archive, parchive = recovery.run_checkpointed(
        T0, snapshots, caller, cfg, store, crash_at=crash_at,
        replay_spec=replay_spec, clock=clock)
    loaded, _ = persistence.load_state(store + "/state.json")
    accounts.clear()
    accounts.update(loaded)
    return ledger, archive, parchive, caller
