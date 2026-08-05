"""Accounts, positions, and fresh-experiment initialization. All money in Decimal.

Engine parameters are LOADED FROM THE FROZEN CONFIG (Ruling 008.14) — the
config is authoritative; verify_params() re-checks at every coordinator start
and raises on any mismatch (Integrity Halt A category)."""
from decimal import Decimal

from . import config as _config

_CFG_PARAMS = _config.load_config()["parameters"]

COINS = ["BTC", "ETH", "SOL"]
MODELS = ["haiku", "sonnet", "opus"]
ARMS = ["raw", "ta"]  # internal ids; 'ta' is the TA arm in public language
START_CASH = Decimal(_CFG_PARAMS["start_cash_usd"])
FEE_RATE = Decimal(_CFG_PARAMS["fee_rate"])
MAINT_MARGIN = Decimal(_CFG_PARAMS["maintenance_margin"])
MAX_LEVERAGE = Decimal(_CFG_PARAMS["max_leverage"])
MIN_DELTA_USD = Decimal(_CFG_PARAMS["min_delta_usd"])
QTY_DECIMALS = dict(_CFG_PARAMS["qty_decimals"])


class ParamsMismatch(Exception):
    """Engine constants no longer match the frozen config => halt."""


def verify_params(cfg):
    p = cfg["parameters"]
    pairs = [(START_CASH, Decimal(p["start_cash_usd"])),
             (FEE_RATE, Decimal(p["fee_rate"])),
             (MAINT_MARGIN, Decimal(p["maintenance_margin"])),
             (MAX_LEVERAGE, Decimal(p["max_leverage"])),
             (MIN_DELTA_USD, Decimal(p["min_delta_usd"]))]
    if any(a != b for a, b in pairs) or QTY_DECIMALS != p["qty_decimals"]:
        raise ParamsMismatch("engine constants != frozen config parameters")
    return True


def account_id(coin, model, arm):
    return f"{coin.lower()}_{model}_{arm}"


def pair_id(coin, model):
    return f"{coin.lower()}_{model}"


def new_account(coin, model, arm):
    return {
        "id": account_id(coin, model, arm),
        "coin": coin, "model": model, "arm": arm,
        "E": START_CASH,          # realized cash equity (Decimal)
        "qty": Decimal("0"),      # signed executed quantity
        "entry": None,
        "stop": None, "tp": None,
        "lifecycles": [],         # append-only history (Ruling 008.15)
        "active_lifecycle_id": None,
        "lifecycle": None,        # reference to the ACTIVE entry in lifecycles
        "fees_total": Decimal("0"),
        "trades": [],
        "theses": [],             # last 3 {"t","text"}
        "n_decisions": 0,
        "terminal": False,
        "terminal_info": None,    # {"t", "cause"}
    }


def init_accounts():
    return {account_id(c, m, a): new_account(c, m, a)
            for c in COINS for m in MODELS for a in ARMS}


def side(acct):
    if acct["qty"] > 0:
        return "long"
    if acct["qty"] < 0:
        return "short"
    return "flat"


def equity_at(acct, price):
    if acct["qty"] == 0 or acct["entry"] is None:
        return acct["E"]
    return acct["E"] + acct["qty"] * (price - acct["entry"])


def liq_threshold(acct):
    """Decimal liquidation threshold per liquidation_contract; None when flat."""
    q, E, entry = acct["qty"], acct["E"], acct["entry"]
    if q == 0 or entry is None:
        return None
    if q > 0:
        return (q * entry - E) / ((Decimal("1") - MAINT_MARGIN) * q)
    return (q * entry - E) / ((Decimal("1") + MAINT_MARGIN) * q)


def is_liquidatable(acct, price):
    if acct["qty"] == 0:
        return False
    return equity_at(acct, price) <= MAINT_MARGIN * abs(acct["qty"]) * price
