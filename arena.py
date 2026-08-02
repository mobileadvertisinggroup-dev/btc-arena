#!/usr/bin/env python3
"""BTC Arena — three Claude models autonomously paper-trade Bitcoin.

Each trader (Haiku 4.5, Sonnet 5, Opus 4.8) gets $10,000 of paper money and
full freedom: long/short/flat, position size up to 5x leverage, and its own
stop-loss / take-profit levels. Nobody tells them how to trade.

Commands:
  tick       (default) enforce stops on 1m candles, snapshot equity, run an
             hourly decision round if due, regenerate the dashboard
  round      force a decision round now
  status     print standings to the terminal
  dashboard  regenerate dashboard.html only
  reset      wipe state and start the game over (asks via --yes flag)

The API key is read from $ANTHROPIC_API_KEY or the .env file next to this
script (line: ANTHROPIC_API_KEY=sk-ant-...). Without a key the arena still
ticks prices but the traders sit idle.

Set BTC_ARENA_MOCK=1 to run decision rounds with canned decisions (testing).
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
STATE_PATH = os.path.join(DATA, "state.json")
EQUITY_PATH = os.path.join(DATA, "equity.jsonl")
DECISIONS_PATH = os.path.join(DATA, "decisions.jsonl")
LOG_PATH = os.path.join(DATA, "arena.log")
LOCK_PATH = os.path.join(DATA, "tick.lock")
DASHBOARD_PATH = os.path.join(BASE, "dashboard.html")

START_CASH = 10_000.0
MAX_LEVERAGE = 5.0
FEE_RATE = 0.0005            # 0.05% taker fee per side
MAINT_MARGIN = 0.02          # forced liquidation at 2% maintenance margin
DECISION_INTERVAL_MIN = 55   # run a round if >=55 min since the last one
MIN_ORDER_USD = 10.0

TRADERS = [
    # raw arm — sees only raw candles
    {"id": "haiku",  "display": "Moudir (Haiku 4.5)", "api_model": "claude-haiku-4-5", "ta": False},
    {"id": "sonnet", "display": "Jamil (Sonnet 5)",   "api_model": "claude-sonnet-5", "ta": False},
    {"id": "opus",   "display": "Ziad (Opus 4.8)",    "api_model": "claude-opus-4-8", "ta": False},
    # TA arm — same models, same prompt, but also gets a technical-indicator
    # pack + Fear & Greed sentiment. The A/B: does the feature pack help?
    {"id": "haiku_ta",  "display": "Moudir+ (Haiku 4.5 TA)", "api_model": "claude-haiku-4-5", "ta": True},
    {"id": "sonnet_ta", "display": "Jamil+ (Sonnet 5 TA)",   "api_model": "claude-sonnet-5", "ta": True},
    {"id": "opus_ta",   "display": "Ziad+ (Opus 4.8 TA)",    "api_model": "claude-opus-4-8", "ta": True},
]

MOCK = os.environ.get("BTC_ARENA_MOCK") == "1"


# ---------------------------------------------------------------- utilities

def now_utc():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now_utc()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def log(msg):
    line = f"{iso()} {msg}"
    print(line)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def append_jsonl(path, obj):
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")


def read_jsonl(path, last_n=None):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = f.readlines()
    if last_n:
        lines = lines[-last_n:]
    out = []
    for ln in lines:
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return out


def get_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    env_path = os.path.join(BASE, ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return None


# ---------------------------------------------------------------- market data

def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "btc-arena/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def binance_klines(interval, limit):
    url = (f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT"
           f"&interval={interval}&limit={limit}")
    raw = http_get_json(url)
    return [{"t": int(k[0] // 1000), "o": float(k[1]), "h": float(k[2]),
             "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in raw]


def kraken_klines(interval_min, limit):
    url = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={interval_min}"
    raw = http_get_json(url)
    key = [k for k in raw["result"] if k != "last"][0]
    rows = raw["result"][key][-limit:]
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]),
             "l": float(r[3]), "c": float(r[4]), "v": float(r[6])} for r in rows]


def fetch_klines(interval, limit):
    """interval: '1m' | '1h' | '1d'."""
    mins = {"1m": 1, "1h": 60, "1d": 1440}[interval]
    # Binance blocks US IPs (e.g. GitHub Actions runners) — prefer Kraken there.
    order = ([lambda: kraken_klines(mins, limit),
              lambda: binance_klines(interval, limit)]
             if os.environ.get("BTC_ARENA_PREFER") == "kraken" else
             [lambda: binance_klines(interval, limit),
              lambda: kraken_klines(mins, limit)])
    try:
        return order[0]()
    except Exception as e:
        log(f"primary exchange failed ({e}); using fallback")
        return order[1]()


# ---------------------------------------------------------------- indicators

def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def ema_series(vals, n):
    k = 2 / (n + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(vals, n=14):
    if len(vals) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = vals[i] - vals[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def macd(vals):
    if len(vals) < 35:
        return None
    line = [a - b for a, b in zip(ema_series(vals, 12), ema_series(vals, 26))]
    signal = ema_series(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]


def bollinger(vals, n=20, k=2.0):
    if len(vals) < n:
        return None
    window = vals[-n:]
    mid = sum(window) / n
    sd = (sum((v - mid) ** 2 for v in window) / n) ** 0.5
    return mid + k * sd, mid, mid - k * sd


def atr(candles, n=14):
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def fetch_fear_greed():
    try:
        d = http_get_json("https://api.alternative.me/fng/", timeout=10)
        row = d["data"][0]
        return f"{row['value']} ({row['value_classification']})"
    except Exception:
        return None


def build_ta_block(hourly, daily):
    """The TA arm's extra briefing. Hourly closes unless noted."""
    hc = [c["c"] for c in hourly]
    dc = [c["c"] for c in daily]
    lines = ["=== TECHNICAL INDICATORS (hourly unless noted) ==="]

    def f(v, nd=1):
        return f"{v:.{nd}f}" if v is not None else "n/a"

    r = rsi(hc)
    lines.append(f"RSI(14): {f(r)}")
    m = macd(hc)
    if m:
        lines.append(f"MACD(12,26,9): line {m[0]:+.1f}  signal {m[1]:+.1f}  "
                     f"histogram {m[2]:+.1f}")
    b = bollinger(hc)
    if b:
        lines.append(f"Bollinger(20,2): upper {b[0]:.0f}  mid {b[1]:.0f}  "
                     f"lower {b[2]:.0f}")
    lines.append(f"SMA 20/50/200: {f(sma(hc, 20), 0)} / {f(sma(hc, 50), 0)} / "
                 f"{f(sma(hc, 200), 0)}")
    e12, e26 = ema_series(hc, 12)[-1], ema_series(hc, 26)[-1]
    lines.append(f"EMA 12/26: {e12:.0f} / {e26:.0f}")
    lines.append(f"ATR(14): {f(atr(hourly), 0)}")
    lines.append(f"Daily SMA 20/50: {f(sma(dc, 20), 0)} / {f(sma(dc, 50), 0)}")
    lines.append(f"Daily RSI(14): {f(rsi(dc))}")
    fg = fetch_fear_greed()
    if fg:
        lines.append(f"Crypto Fear & Greed Index: {fg}")
    return "\n".join(lines)


# ---------------------------------------------------------------- state

def new_trader(tconf):
    return {
        "display": tconf["display"], "api_model": tconf["api_model"],
        "equity": START_CASH,        # realized equity (cash basis)
        "qty": 0.0,                  # BTC; + long, - short
        "entry": None, "stop": None, "tp": None,
        "opened_at": None,
        "invalidation": None,        # the model's own "what proves me wrong"
        "liquidated": False,
        "trades": [],                # closed trades
        "n_decisions": 0,
        "last_reasonings": [],       # last 3, newest last
    }


def default_state():
    return {"started_at": iso(), "last_decision_at": None,
            "last_tick_at": None, "last_price": None,
            "traders": {t["id"]: new_trader(t) for t in TRADERS}}


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)
    else:
        state = default_state()
    # sync roster with the TRADERS config: renames apply to a live game and
    # newly added traders join with a fresh $10k account
    for tc in TRADERS:
        if tc["id"] in state["traders"]:
            state["traders"][tc["id"]]["display"] = tc["display"]
            state["traders"][tc["id"]].setdefault("invalidation", None)
        else:
            state["traders"][tc["id"]] = new_trader(tc)
    return state


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_PATH)


# ---------------------------------------------------------------- accounting

def side_of(t):
    if t["qty"] > 0:
        return "long"
    if t["qty"] < 0:
        return "short"
    return "flat"


def equity_total(t, price):
    if t["qty"] == 0 or t["entry"] is None:
        return t["equity"]
    return t["equity"] + t["qty"] * (price - t["entry"])


def liq_price(t):
    """Price at which equity hits maintenance margin. None if flat."""
    q, eq, entry = t["qty"], t["equity"], t["entry"]
    if q == 0 or entry is None:
        return None
    if q > 0:
        return (q * entry - eq) / ((1 - MAINT_MARGIN) * q)
    return (q * entry - eq) / ((1 + MAINT_MARGIN) * q)


def close_position(t, price, reason):
    q = t["qty"]
    if q == 0:
        return 0.0
    pnl = q * (price - t["entry"])
    fee = abs(q) * price * FEE_RATE
    t["equity"] += pnl - fee
    t["trades"].append({
        "opened_at": t["opened_at"], "closed_at": iso(),
        "side": side_of(t), "qty": round(abs(q), 6),
        "entry": round(t["entry"], 2), "exit": round(price, 2),
        "pnl": round(pnl - fee, 2), "reason": reason,
    })
    t["qty"] = 0.0
    t["entry"] = None
    t["stop"] = None
    t["tp"] = None
    t["opened_at"] = None
    if t["equity"] <= 0:
        t["equity"] = 0.0
        t["liquidated"] = True
    return pnl - fee


def open_position(t, side, notional, price):
    qty = notional / price * (1 if side == "long" else -1)
    fee = notional * FEE_RATE
    t["equity"] -= fee
    t["qty"] = qty
    t["entry"] = price
    t["opened_at"] = iso()


def resize_position(t, notional, price):
    """Same-side resize to target notional."""
    sign = 1 if t["qty"] > 0 else -1
    new_qty = sign * notional / price
    delta = new_qty - t["qty"]
    fee = abs(delta) * price * FEE_RATE
    if abs(new_qty) > abs(t["qty"]):
        t["entry"] = ((abs(t["qty"]) * t["entry"] + abs(delta) * price)
                      / abs(new_qty))
    else:
        reduced = t["qty"] - new_qty
        t["equity"] += reduced * (price - t["entry"])
    t["equity"] -= fee
    t["qty"] = new_qty


# ---------------------------------------------------------------- tick logic

def enforce_exits(state, candles):
    """Walk 1m candles chronologically; enforce stops, TPs, liquidations."""
    for tid, t in state["traders"].items():
        if t["qty"] == 0 or t["liquidated"]:
            continue
        for c in candles:
            if t["qty"] == 0:
                break
            lp = liq_price(t)
            if t["qty"] > 0:  # long
                trigger, reason = None, None
                if t["stop"] is not None:
                    trigger, reason = t["stop"], "stop_loss"
                if lp is not None and (trigger is None or lp > trigger):
                    trigger, reason = lp, "liquidation"
                if trigger is not None and c["l"] <= trigger:
                    px = min(c["o"], trigger) if c["o"] < trigger else trigger
                    close_position(t, px, reason)
                    log(f"{tid}: {reason} @ {px:.2f}")
                    continue
                if t["tp"] is not None and c["h"] >= t["tp"]:
                    px = max(c["o"], t["tp"]) if c["o"] > t["tp"] else t["tp"]
                    close_position(t, px, "take_profit")
                    log(f"{tid}: take_profit @ {px:.2f}")
            else:  # short
                trigger, reason = None, None
                if t["stop"] is not None:
                    trigger, reason = t["stop"], "stop_loss"
                if lp is not None and (trigger is None or lp < trigger):
                    trigger, reason = lp, "liquidation"
                if trigger is not None and c["h"] >= trigger:
                    px = max(c["o"], trigger) if c["o"] > trigger else trigger
                    close_position(t, px, reason)
                    log(f"{tid}: {reason} @ {px:.2f}")
                    continue
                if t["tp"] is not None and c["l"] <= t["tp"]:
                    px = min(c["o"], t["tp"]) if c["o"] < t["tp"] else t["tp"]
                    close_position(t, px, "take_profit")
                    log(f"{tid}: take_profit @ {px:.2f}")


# ---------------------------------------------------------------- claude api

DECISION_TOOL = {
    "name": "submit_decision",
    "description": ("Submit your trading decision for this round. You are "
                    "fully autonomous: choose your direction, size, and "
                    "optionally your own stop-loss and take-profit levels."),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "position": {
                "type": "string", "enum": ["long", "short", "flat"],
                "description": ("Target position. 'flat' closes everything. "
                                "If you already hold this side, size_usd "
                                "adjusts the position; stop/tp are updated."),
            },
            "size_usd": {
                "type": "number",
                "description": ("Target position notional in USD (0 if flat). "
                                "Capped at 5x your current equity."),
            },
            "stop_loss": {
                "type": ["number", "null"],
                "description": ("Optional stop-loss price. null = no stop. "
                                "Checked every ~5 minutes against 1m candles."),
            },
            "take_profit": {
                "type": ["number", "null"],
                "description": "Optional take-profit price. null = none.",
            },
            "reasoning": {
                "type": "string",
                "description": ("Brief explanation of your thinking this "
                                "round (2-5 sentences). Shown on the public "
                                "leaderboard."),
            },
            "invalidation": {
                "type": "string",
                "description": ("The specific, observable market condition "
                                "that would prove this thesis WRONG (e.g. "
                                "'hourly close below 62500' or '2 consecutive "
                                "daily closes above the 20-day SMA'). If you "
                                "are flat, state what would make you enter. "
                                "You will be shown this next round and must "
                                "confront it honestly."),
            },
        },
        "required": ["position", "size_usd", "stop_loss", "take_profit",
                     "reasoning", "invalidation"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You are an autonomous trader in a live competition. You manage a paper-money account trading a BTC-USD perpetual against real live Bitcoin prices. Two other AI models trade the same market with the same rules; highest equity wins. There is no end date — trade like the account is yours.

Rules of the venue:
- You start with $10,000. You may go long, short, or flat. Max leverage 5x equity.
- Taker fee 0.05% of notional per execution (open, close, resize).
- You are consulted roughly once per hour. Between decisions your stop-loss and take-profit orders (if set) are enforced against 1-minute candles every ~5 minutes.
- If equity falls to the 2% maintenance margin your position is force-liquidated. Equity at $0 means you are out of the game permanently.
- No funding rates, no slippage; fills at the traded price.

You are completely free: strategy, timeframe, risk management, whether to use stops at all — every choice is yours. You will see your own past reasoning to maintain continuity.

Accountability rule: every decision must include an invalidation signal — the specific market condition that would prove your thesis wrong. Next round you will be shown your stated invalidation and are expected to confront it honestly: if it has triggered, act accordingly rather than moving the goalposts. Submit your decision with the submit_decision tool."""


def build_user_prompt(t, market, ta=False):
    h = market["hourly"]
    d = market["daily"]
    price = market["price"]
    chg24 = (price / h[-25]["c"] - 1) * 100 if len(h) >= 25 else 0.0
    lines = []
    lines.append(f"Time now: {iso()}")
    lines.append(f"BTC-USD price: {price:.2f}  (24h change: {chg24:+.2f}%)")
    lines.append("")
    lines.append("Last 24 hourly candles (time open high low close volume):")
    for c in h[-24:]:
        ts = datetime.fromtimestamp(c["t"], timezone.utc).strftime("%m-%d %H:%M")
        lines.append(f"{ts} {c['o']:.0f} {c['h']:.0f} {c['l']:.0f} {c['c']:.0f} {c['v']:.0f}")
    lines.append("")
    lines.append("Last 30 daily closes (oldest first): "
                 + " ".join(f"{c['c']:.0f}" for c in d[-30:]))
    lines.append("")
    if ta and market.get("ta_block"):
        lines.append(market["ta_block"])
        lines.append("")
    eq = equity_total(t, price)
    lines.append("=== YOUR ACCOUNT ===")
    lines.append(f"Equity: ${eq:.2f}  (started with $10,000)")
    if t["qty"] != 0:
        notional = abs(t["qty"]) * price
        upnl = t["qty"] * (price - t["entry"])
        lines.append(f"Position: {side_of(t)} {abs(t['qty']):.5f} BTC "
                     f"(${notional:.0f} notional) entry {t['entry']:.2f} "
                     f"unrealized P&L {upnl:+.2f}")
        lines.append(f"Stop-loss: {t['stop']}, Take-profit: {t['tp']}")
        lines.append(f"Liquidation price: ~{liq_price(t):.0f}")
    else:
        lines.append("Position: flat")
    lines.append(f"Max position size right now: ${MAX_LEVERAGE * eq:.0f} notional")
    if t["trades"]:
        lines.append("Recent closed trades:")
        for tr in t["trades"][-5:]:
            lines.append(f"  {tr['side']} entry {tr['entry']} exit {tr['exit']} "
                         f"P&L {tr['pnl']:+.2f} ({tr['reason']})")
    if t.get("invalidation"):
        lines.append(f"Your stated invalidation signal from last round: "
                     f"\"{t['invalidation']}\" — has it triggered? Confront "
                     f"it honestly before deciding.")
    if t["last_reasonings"]:
        lines.append("Your reasoning from previous rounds (oldest first):")
        for r in t["last_reasonings"]:
            lines.append(f"  [{r['t']}] {r['text']}")
    return "\n".join(lines)


MOCK_DECISIONS = {
    "haiku": {"position": "long", "size_usd": 5000, "stop_loss": None,
              "take_profit": None, "reasoning": "Mock: testing a long.",
              "invalidation": "Mock: close below entry - 3%."},
    "sonnet": {"position": "short", "size_usd": 3000, "stop_loss": None,
               "take_profit": None, "reasoning": "Mock: testing a short.",
               "invalidation": "Mock: close above entry + 3%."},
}
MOCK_DEFAULT = {"position": "flat", "size_usd": 0, "stop_loss": None,
                "take_profit": None, "reasoning": "Mock: staying flat.",
                "invalidation": "Mock: breakout either way."}


def call_model(api_key, tid, api_model, user_prompt):
    if MOCK:
        d = dict(MOCK_DECISIONS.get(tid, MOCK_DEFAULT))
        return d, {"mock": True}
    body = {
        "model": api_model,
        "max_tokens": 1500,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [DECISION_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_decision"},
    }
    if api_model == "claude-sonnet-5":
        # Sonnet 5 runs adaptive thinking by default, which is incompatible
        # with a forced tool_choice — turn it off explicitly.
        body["thinking"] = {"type": "disabled"}
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=data, method="POST",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read().decode())
            for block in resp.get("content", []):
                if block.get("type") == "tool_use":
                    return block["input"], resp.get("usage", {})
            raise ValueError(f"no tool_use block (stop_reason="
                             f"{resp.get('stop_reason')})")
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            last_err = f"HTTP {e.code}: {detail}"
            if e.code in (429, 500, 529) and attempt < 2:
                time.sleep(15 * (attempt + 1))
                continue
            break
        except Exception as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(10)
                continue
    raise RuntimeError(last_err)


# ---------------------------------------------------------------- decisions

def valid_level(v, lo=1000, hi=10_000_000):
    return isinstance(v, (int, float)) and lo < v < hi


def execute_decision(t, dec, price):
    """Apply a model's decision to its account. Returns action summary."""
    if t["liquidated"]:
        return "liquidated — out of the game"
    actions = []
    target = dec.get("position")
    size = dec.get("size_usd") or 0
    if not isinstance(size, (int, float)) or size < 0:
        size = 0
    cur = side_of(t)

    if target == "flat":
        if cur != "flat":
            pnl = close_position(t, price, "decision_close")
            actions.append(f"closed {cur} (P&L {pnl:+.2f})")
        else:
            actions.append("stayed flat")
        return "; ".join(actions)

    if target not in ("long", "short"):
        return f"invalid position '{target}' — no action"

    if cur != "flat" and cur != target:
        pnl = close_position(t, price, "decision_flip")
        actions.append(f"closed {cur} (P&L {pnl:+.2f})")
        cur = "flat"
    if t["liquidated"]:
        return "; ".join(actions) + "; liquidated on close"

    eq = equity_total(t, price)
    size = min(size, MAX_LEVERAGE * eq)

    if cur == "flat":
        if size >= MIN_ORDER_USD:
            open_position(t, target, size, price)
            actions.append(f"opened {target} ${size:.0f} @ {price:.2f}")
        else:
            actions.append("size too small — stayed flat")
    else:
        cur_notional = abs(t["qty"]) * price
        if size >= MIN_ORDER_USD and abs(size - cur_notional) / cur_notional > 0.05:
            resize_position(t, size, price)
            actions.append(f"resized {target} to ${size:.0f}")
        else:
            actions.append(f"held {target}")

    if t["qty"] != 0:
        sl, tp = dec.get("stop_loss"), dec.get("take_profit")
        if sl is None:
            t["stop"] = None
        elif valid_level(sl) and ((t["qty"] > 0 and sl < price) or
                                  (t["qty"] < 0 and sl > price)):
            t["stop"] = float(sl)
        else:
            actions.append(f"ignored invalid stop {sl}")
        if tp is None:
            t["tp"] = None
        elif valid_level(tp) and ((t["qty"] > 0 and tp > price) or
                                  (t["qty"] < 0 and tp < price)):
            t["tp"] = float(tp)
        else:
            actions.append(f"ignored invalid tp {tp}")
        if t["stop"] is not None or t["tp"] is not None:
            actions.append(f"stop={t['stop']} tp={t['tp']}")
    inv = dec.get("invalidation")
    t["invalidation"] = str(inv)[:500] if inv else None
    return "; ".join(actions)


def run_decision_round(state, market):
    api_key = get_api_key()
    if not api_key and not MOCK:
        log("no API key — skipping decision round (traders idle)")
        return False
    price = market["price"]
    for tconf in TRADERS:
        tid = tconf["id"]
        t = state["traders"][tid]
        if t["liquidated"]:
            continue
        prompt = build_user_prompt(t, market, ta=tconf.get("ta", False))
        try:
            dec, usage = call_model(api_key, tid, tconf["api_model"], prompt)
        except Exception as e:
            log(f"{tid}: model call failed: {e}")
            append_jsonl(DECISIONS_PATH, {"t": iso(), "trader": tid,
                                          "price": round(price, 2),
                                          "error": str(e)[:300]})
            continue
        executed = execute_decision(t, dec, price)
        t["n_decisions"] += 1
        reasoning = str(dec.get("reasoning", ""))[:2000]
        t["last_reasonings"] = (t["last_reasonings"]
                                + [{"t": iso(), "text": reasoning}])[-3:]
        append_jsonl(DECISIONS_PATH, {
            "t": iso(), "trader": tid, "price": round(price, 2),
            "decision": {k: dec.get(k) for k in
                         ("position", "size_usd", "stop_loss", "take_profit")},
            "reasoning": reasoning,
            "invalidation": str(dec.get("invalidation", ""))[:500],
            "executed": executed,
            "equity_after": round(equity_total(t, price), 2),
            "usage": {k: usage.get(k) for k in
                      ("input_tokens", "output_tokens")} if usage else None,
        })
        log(f"{tid}: {dec.get('position')} ${dec.get('size_usd')} -> {executed}")
    state["last_decision_at"] = iso()
    return True


# ---------------------------------------------------------------- tick

def acquire_lock():
    if os.path.exists(LOCK_PATH):
        age = time.time() - os.path.getmtime(LOCK_PATH)
        if age < 600:
            return False
    with open(LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_lock():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def tick(force_round=False):
    os.makedirs(DATA, exist_ok=True)
    if not acquire_lock():
        log("another tick is running — skipping")
        return
    try:
        state = load_state()
        # 1m candles since last tick for stop enforcement
        last = state.get("last_tick_at")
        minutes = 10
        if last:
            minutes = min(120, max(2, int((now_utc() - parse_iso(last))
                                          .total_seconds() // 60) + 2))
        candles_1m = fetch_klines("1m", minutes)
        price = candles_1m[-1]["c"]
        if last:
            cutoff = parse_iso(last).timestamp()
            enforce_exits(state, [c for c in candles_1m if c["t"] >= cutoff])

        # decision round if due
        due = force_round or state["last_decision_at"] is None or \
            (now_utc() - parse_iso(state["last_decision_at"])).total_seconds() \
            >= DECISION_INTERVAL_MIN * 60
        if due:
            market = {"price": price,
                      "hourly": fetch_klines("1h", 260),
                      "daily": fetch_klines("1d", 60)}
            market["ta_block"] = build_ta_block(market["hourly"],
                                               market["daily"])
            run_decision_round(state, market)
            price = market["price"]

        # equity snapshot
        snap = {"t": iso(), "price": round(price, 2)}
        for tid, t in state["traders"].items():
            snap[tid] = round(equity_total(t, price), 2)
        append_jsonl(EQUITY_PATH, snap)

        state["last_tick_at"] = iso()
        state["last_price"] = round(price, 2)
        save_state(state)
        gen_dashboard(state)
    finally:
        release_lock()


# ---------------------------------------------------------------- dashboard

def gen_dashboard(state):
    equity_rows = read_jsonl(EQUITY_PATH, last_n=4032)  # ~14 days at 5min
    decisions = read_jsonl(DECISIONS_PATH, last_n=60)
    payload = {
        "generated_at": iso(),
        "price": state.get("last_price"),
        "started_at": state.get("started_at"),
        "last_decision_at": state.get("last_decision_at"),
        "has_key": bool(get_api_key()),
        "start_cash": START_CASH,
        "traders": [],
        "equity": equity_rows,
        "decisions": decisions[::-1],  # newest first
    }
    price = state.get("last_price") or 0
    for tconf in TRADERS:
        t = state["traders"][tconf["id"]]
        eq = equity_total(t, price) if price else t["equity"]
        payload["traders"].append({
            "id": tconf["id"], "display": t["display"],
            "equity": round(eq, 2),
            "pnl_pct": round((eq / START_CASH - 1) * 100, 2),
            "side": side_of(t),
            "notional": round(abs(t["qty"]) * price, 0) if t["qty"] else 0,
            "entry": t["entry"], "stop": t["stop"], "tp": t["tp"],
            "invalidation": t.get("invalidation"),
            "liquidated": t["liquidated"],
            "n_decisions": t["n_decisions"],
            "n_trades": len(t["trades"]),
            "trades": t["trades"][-8:][::-1],
        })
    html = DASHBOARD_TEMPLATE.replace("__DATA__", json.dumps(payload))
    tmp = DASHBOARD_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(html)
    os.replace(tmp, DASHBOARD_PATH)


DASHBOARD_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>BTC Arena</title>
<style>
:root {
  --surface: #fcfcfb; --page: #f9f9f7;
  --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --c-haiku: #2a78d6; --c-sonnet: #1baf7a; --c-opus: #eda100;
  --c-haiku_ta: #4a3aa7; --c-sonnet_ta: #e34948; --c-opus_ta: #eb6834;
  --up: #006300; --down: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #1a1a19; --page: #0d0d0d;
    --ink: #ffffff; --ink2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --c-haiku: #3987e5; --c-sonnet: #199e70; --c-opus: #c98500;
    --c-haiku_ta: #9085e9; --c-sonnet_ta: #e66767; --c-opus_ta: #d95926;
    --up: #0ca30c; --down: #e66767;
  }
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--page); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: 20px; max-width: 1100px; margin: 0 auto; }
h1 { font-size: 20px; font-weight: 650; }
.sub { color: var(--ink2); font-size: 13px; margin-top: 2px; }
.banner { background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 10px 14px; margin-top: 14px;
  color: var(--ink2); font-size: 13px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px,1fr));
  gap: 12px; margin-top: 16px; }
.tile { background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 14px 16px; }
.tile .name { display: flex; align-items: center; gap: 8px;
  font-weight: 600; font-size: 14px; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.tile .eq { font-size: 26px; font-weight: 650; margin-top: 6px; }
.tile .pnl { font-size: 14px; font-weight: 600; }
.pos { color: var(--ink2); font-size: 12.5px; margin-top: 6px; }
.up { color: var(--up); } .down { color: var(--down); }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px; margin-top: 16px; }
.card h2 { font-size: 14px; font-weight: 650; margin-bottom: 10px; }
.legend { display: flex; gap: 16px; font-size: 12.5px; color: var(--ink2);
  margin-bottom: 8px; flex-wrap: wrap; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
#chartwrap { position: relative; }
#tip { position: absolute; pointer-events: none; display: none;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px; font-size: 12px;
  box-shadow: 0 2px 10px rgba(0,0,0,.12); white-space: nowrap; z-index: 2; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th { text-align: left; color: var(--muted); font-weight: 500;
  padding: 4px 10px 4px 0; border-bottom: 1px solid var(--grid); }
td { padding: 5px 10px 5px 0; border-bottom: 1px solid var(--grid);
  vertical-align: top; font-variant-numeric: tabular-nums; }
.feed .item { border-bottom: 1px solid var(--grid); padding: 10px 0; }
.feed .item:last-child { border-bottom: none; }
.feed .head { display: flex; gap: 8px; align-items: baseline;
  flex-wrap: wrap; font-size: 12.5px; color: var(--ink2); }
.feed .who { font-weight: 600; color: var(--ink); display: inline-flex;
  align-items: center; gap: 6px; }
.feed .reason { margin-top: 4px; font-size: 13px; }
.feed .exec { margin-top: 3px; font-size: 12px; color: var(--muted); }
</style>
</head>
<body>
<h1>&#8383; BTC Arena — Haiku vs Sonnet vs Opus</h1>
<div class="sub" id="subtitle"></div>
<div id="banner"></div>
<div class="tiles" id="tiles"></div>
<div class="card">
  <h2>Equity — $10,000 start, live BTC prices</h2>
  <div class="legend" id="legend"></div>
  <div id="chartwrap"><svg id="chart" width="100%" height="320"></svg>
    <div id="tip"></div></div>
</div>
<div class="card feed"><h2>Decision feed (what each AI was thinking)</h2>
  <div id="feed"></div></div>
<div class="card"><h2>Closed trades</h2>
  <div class="tablewrap"><table id="trades"><thead><tr>
    <th>Trader</th><th>Side</th><th>Entry</th><th>Exit</th>
    <th>P&amp;L</th><th>Reason</th><th>Closed</th>
  </tr></thead><tbody></tbody></table></div></div>
<script>
const D = __DATA__;
const COLORS = Object.fromEntries(D.traders.map(t => [t.id, `var(--c-${t.id})`]));
const SHORT = Object.fromEntries(D.traders.map(t => [t.id, t.display.split(' ')[0]]));
const fmt$ = v => '$' + v.toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 0});
const fmt2 = v => '$' + v.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const ftime = s => new Date(s).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});

document.getElementById('subtitle').textContent =
  'BTC ' + (D.price ? fmt$(D.price) : '—') +
  ' · updated ' + ftime(D.generated_at) +
  (D.last_decision_at ? ' · last decision round ' + ftime(D.last_decision_at) : '') +
  ' · started ' + ftime(D.started_at);

if (!D.has_key) {
  document.getElementById('banner').innerHTML =
    '<div class="banner">&#9888;&#65039; Waiting for an Anthropic API key — the arena is ticking prices but the traders are idle. Add the key to btc-arena/.env to start the game.</div>';
}

// standings tiles, ranked
const ranked = [...D.traders].sort((a,b) => b.equity - a.equity);
document.getElementById('tiles').innerHTML = ranked.map((t,i) => {
  const cls = t.pnl_pct >= 0 ? 'up' : 'down';
  let pos = t.liquidated ? '&#128128; LIQUIDATED — out of the game'
    : t.side === 'flat' ? 'Flat — no position'
    : t.side.toUpperCase() + ' ' + fmt$(t.notional) + ' @ ' + fmt$(t.entry)
      + (t.stop ? ' · stop ' + fmt$(t.stop) : ' · no stop')
      + (t.tp ? ' · tp ' + fmt$(t.tp) : '');
  return `<div class="tile">
    <div class="name"><span class="dot" style="background:${COLORS[t.id]}"></span>
      ${['&#129351;','&#129352;','&#129353;'][i] || ''} ${t.display}</div>
    <div class="eq">${fmt2(t.equity)}</div>
    <div class="pnl ${cls}">${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%</div>
    <div class="pos">${pos}</div>
    ${t.invalidation ? `<div class="pos">Invalidation: ${t.invalidation.replace(/</g,'&lt;')}</div>` : ''}
    <div class="pos">${t.n_decisions} decisions · ${t.n_trades} closed trades</div>
  </div>`;
}).join('');

document.getElementById('legend').innerHTML = D.traders.map(t =>
  `<span><span class="dot" style="background:${COLORS[t.id]}"></span>${t.display}</span>`
).join('') + '<span style="color:var(--muted)">dashed = $10,000 start · "+" traders get technical indicators &amp; sentiment, plain traders get raw candles only</span>';

// equity chart
(function chart() {
  const rows = D.equity;
  const svg = document.getElementById('chart');
  if (rows.length < 2) {
    svg.outerHTML = '<div class="pos">Not enough data yet — the chart appears after a few ticks.</div>';
    return;
  }
  const W = 1060, H = 320, ML = 56, MR = 70, MT = 12, MB = 26;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const keys = D.traders.map(t => t.id).filter(k => rows.some(r => typeof r[k] === 'number'));
  const t0 = new Date(rows[0].t).getTime(), t1 = new Date(rows[rows.length-1].t).getTime();
  let lo = Infinity, hi = -Infinity;
  for (const r of rows) for (const k of keys) {
    if (typeof r[k] === 'number') { lo = Math.min(lo, r[k]); hi = Math.max(hi, r[k]); }
  }
  lo = Math.min(lo, D.start_cash); hi = Math.max(hi, D.start_cash);
  const pad = Math.max((hi - lo) * 0.08, 40); lo -= pad; hi += pad;
  const X = t => ML + (W - ML - MR) * (t - t0) / Math.max(1, t1 - t0);
  const Y = v => MT + (H - MT - MB) * (1 - (v - lo) / (hi - lo));
  let g = '';
  // gridlines + y labels
  const steps = 4;
  for (let i = 0; i <= steps; i++) {
    const v = lo + (hi - lo) * i / steps, y = Y(v);
    g += `<line x1="${ML}" y1="${y}" x2="${W-MR}" y2="${y}" stroke="var(--grid)" stroke-width="1"/>`;
    g += `<text x="${ML-8}" y="${y+4}" text-anchor="end" font-size="11" fill="var(--muted)">${fmt$(v)}</text>`;
  }
  // x labels
  for (let i = 0; i <= 4; i++) {
    const t = t0 + (t1 - t0) * i / 4;
    g += `<text x="${X(t)}" y="${H-8}" text-anchor="middle" font-size="11" fill="var(--muted)">${new Date(t).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}</text>`;
  }
  // start line
  g += `<line x1="${ML}" y1="${Y(D.start_cash)}" x2="${W-MR}" y2="${Y(D.start_cash)}" stroke="var(--axis)" stroke-width="1" stroke-dasharray="4 4"/>`;
  // series
  const lastRow = rows[rows.length-1];
  for (const k of keys) {
    const pts = rows.filter(r => typeof r[k] === 'number')
      .map(r => `${X(new Date(r.t).getTime()).toFixed(1)},${Y(r[k]).toFixed(1)}`);
    g += `<polyline points="${pts.join(' ')}" fill="none" stroke="${COLORS[k]}" stroke-width="2" stroke-linejoin="round"/>`;
    if (typeof lastRow[k] === 'number')
      g += `<text x="${W-MR+6}" y="${Y(lastRow[k])+4}" font-size="11.5" font-weight="600" fill="${COLORS[k]}">${SHORT[k]}</text>`;
  }
  g += `<line id="xhair" x1="0" y1="${MT}" x2="0" y2="${H-MB}" stroke="var(--axis)" stroke-width="1" visibility="hidden"/>`;
  svg.innerHTML = g;
  // hover crosshair + tooltip
  const tip = document.getElementById('tip');
  svg.addEventListener('mousemove', ev => {
    const rect = svg.getBoundingClientRect();
    const px = (ev.clientX - rect.left) / rect.width * W;
    if (px < ML || px > W - MR) { tip.style.display = 'none'; return; }
    const tt = t0 + (px - ML) / (W - ML - MR) * (t1 - t0);
    let best = 0, bd = Infinity;
    rows.forEach((r, i) => { const d = Math.abs(new Date(r.t).getTime() - tt);
      if (d < bd) { bd = d; best = i; } });
    const r = rows[best];
    const xh = document.getElementById('xhair');
    const cx = X(new Date(r.t).getTime());
    xh.setAttribute('x1', cx); xh.setAttribute('x2', cx);
    xh.setAttribute('visibility', 'visible');
    tip.innerHTML = `<b>${ftime(r.t)}</b> · BTC ${fmt$(r.price)}<br>` +
      keys.filter(k => typeof r[k] === 'number')
        .map(k => `<span style="color:${COLORS[k]}">&#9679;</span> ${SHORT[k]}: ${fmt2(r[k])}`).join('<br>');
    tip.style.display = 'block';
    const left = Math.min(ev.clientX - rect.left + 14, rect.width - 190);
    tip.style.left = left + 'px';
    tip.style.top = '18px';
  });
  svg.addEventListener('mouseleave', () => {
    tip.style.display = 'none';
    document.getElementById('xhair').setAttribute('visibility', 'hidden');
  });
})();

// decision feed
const nameOf = id => (D.traders.find(t => t.id === id) || {display: id}).display;
document.getElementById('feed').innerHTML = D.decisions.length === 0
  ? '<div class="pos">No decisions yet.</div>'
  : D.decisions.map(d => {
    if (d.error) return `<div class="item"><div class="head">
      <span class="who"><span class="dot" style="background:${COLORS[d.trader]}"></span>${nameOf(d.trader)}</span>
      <span>${ftime(d.t)}</span></div>
      <div class="exec">&#9888;&#65039; model call failed: ${d.error}</div></div>`;
    const dec = d.decision || {};
    return `<div class="item"><div class="head">
      <span class="who"><span class="dot" style="background:${COLORS[d.trader]}"></span>${nameOf(d.trader)}</span>
      <span>${ftime(d.t)}</span><span>BTC ${fmt$(d.price)}</span>
      <span><b>${(dec.position||'').toUpperCase()}</b>${dec.size_usd ? ' ' + fmt$(dec.size_usd) : ''}</span>
      <span>equity ${fmt2(d.equity_after)}</span></div>
      <div class="reason">${(d.reasoning||'').replace(/</g,'&lt;')}</div>
      ${d.invalidation ? `<div class="exec">Invalidation: ${d.invalidation.replace(/</g,'&lt;')}</div>` : ''}
      <div class="exec">${(d.executed||'').replace(/</g,'&lt;')}</div></div>`;
  }).join('');

// trades table
const tbody = document.querySelector('#trades tbody');
const allTrades = [];
for (const t of D.traders) for (const tr of t.trades) allTrades.push({...tr, who: t.display, id: t.id});
allTrades.sort((a,b) => (a.closed_at < b.closed_at ? 1 : -1));
tbody.innerHTML = allTrades.length === 0
  ? '<tr><td colspan="7" style="color:var(--muted)">No closed trades yet.</td></tr>'
  : allTrades.map(tr => `<tr>
    <td><span class="dot" style="background:${COLORS[tr.id]};display:inline-block;vertical-align:middle;margin-right:6px"></span>${tr.who}</td>
    <td>${tr.side}</td><td>${fmt$(tr.entry)}</td><td>${fmt$(tr.exit)}</td>
    <td class="${tr.pnl >= 0 ? 'up' : 'down'}">${tr.pnl >= 0 ? '+' : ''}${tr.pnl.toFixed(2)}</td>
    <td>${tr.reason.replace(/_/g,' ')}</td><td>${ftime(tr.closed_at)}</td></tr>`).join('');
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- commands

def cmd_status():
    state = load_state()
    price = state.get("last_price") or 0
    print(f"BTC {price:,.2f}  last tick {state.get('last_tick_at')}  "
          f"last round {state.get('last_decision_at')}")
    for tid, t in sorted(state["traders"].items(),
                         key=lambda kv: -equity_total(kv[1], price)):
        eq = equity_total(t, price)
        pos = side_of(t)
        extra = ""
        if pos != "flat":
            extra = (f" {abs(t['qty']):.5f} BTC @ {t['entry']:.2f} "
                     f"stop={t['stop']} tp={t['tp']}")
        flag = " LIQUIDATED" if t["liquidated"] else ""
        print(f"  {t['display']:<10} ${eq:>10,.2f} ({(eq/START_CASH-1)*100:+.2f}%)"
              f"  {pos}{extra}{flag}")


def main():
    os.makedirs(DATA, exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd == "tick":
        tick()
    elif cmd == "round":
        tick(force_round=True)
    elif cmd == "status":
        cmd_status()
    elif cmd == "dashboard":
        gen_dashboard(load_state())
        print(f"wrote {DASHBOARD_PATH}")
    elif cmd == "reset":
        if "--yes" not in sys.argv:
            print("This wipes the game. Run: arena.py reset --yes")
            return
        for p in (STATE_PATH, EQUITY_PATH, DECISIONS_PATH):
            if os.path.exists(p):
                os.remove(p)
        save_state(default_state())
        print("Arena reset.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
