"""Atomic state persistence, round ledger, heartbeat, restart recovery."""
import json
import os
from decimal import Decimal

STATE_KEYS_DEC = ("E", "qty", "entry", "stop", "tp", "fees_total")


def _enc(acct):
    out = dict(acct)
    out.pop("_mark", None)
    for k in STATE_KEYS_DEC:
        out[k] = str(acct[k]) if acct[k] is not None else None
    if acct.get("lifecycle"):
        lc = dict(acct["lifecycle"])
        inv = dict(lc["invalidation"])
        inv["level"] = str(inv["level"])
        lc["invalidation"] = inv
        out["lifecycle"] = lc
    if acct.get("watch"):
        wc = dict(acct["watch"])
        wc["level"] = str(wc["level"])
        out["watch"] = wc
    return out


def _dec(acct):
    for k in STATE_KEYS_DEC:
        acct[k] = Decimal(acct[k]) if acct[k] is not None else None
    if acct.get("lifecycle"):
        acct["lifecycle"]["invalidation"]["level"] = \
            Decimal(acct["lifecycle"]["invalidation"]["level"])
    if acct.get("watch"):
        acct["watch"]["level"] = Decimal(acct["watch"]["level"])
    return acct


def _enc_all(accounts):
    return {k: _enc(a) for k, a in accounts.items()}


def save_state(path, accounts, meta):
    payload = {"meta": meta,
               "accounts": {k: _enc(a) for k, a in accounts.items()}}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=1, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_state(path):
    with open(path) as f:
        payload = json.load(f)
    accounts = {k: _dec(a) for k, a in payload["accounts"].items()}
    return accounts, payload["meta"]


def append_ledger(path, entries):
    with open(path, "a") as f:
        for e in entries:
            f.write(json.dumps(e, default=str) + "\n")


def read_ledger(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def committed_round_ids(ledger):
    return {e["round_id"] for e in ledger
            if e.get("status") in ("PAIR_COMMITTED", "PAIR_ABORTED",
                                   "PAIR_TERMINAL_SPLIT")}


def boundary_already_processed(ledger, coin, round_id):
    return any(e["round_id"] == round_id for e in ledger)


def write_heartbeat(path, t, code_hash, last_boundary):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"ts": t, "code_hash": code_hash,
                   "last_boundary_processed": last_boundary}, f)
    os.replace(tmp, path)
