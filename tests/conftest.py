import json
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import config, state, marketdata, rounds  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
T0 = 1754870400  # matches gen_fixtures


def load_fix(coin, tf):
    with open(os.path.join(FIX, f"{coin.lower()}_{tf}.json")) as f:
        return json.load(f)


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
        self.script = dict(script)
        self.default = default
        self.calls = []
        self.in_flight_max = 0

    def __call__(self, account_id, system, user, retry_msg):
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
