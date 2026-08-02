"""Accounts, positions, and fresh-experiment initialization. All money in Decimal."""
from decimal import Decimal

COINS = ["BTC", "ETH", "SOL"]
MODELS = ["haiku", "sonnet", "opus"]
ARMS = ["raw", "ta"]  # internal ids; 'ta' is the Feature arm in public language
START_CASH = Decimal("10000.00")
FEE_RATE = Decimal("0.0005")
MAINT_MARGIN = Decimal("0.02")
MAX_LEVERAGE = Decimal("5")
MIN_DELTA_USD = Decimal("10.00")


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
        "lifecycle": None,        # see lifecycle.py
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
