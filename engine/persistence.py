"""Atomic, checksummed, validated state persistence; durable per-attempt files;
transactional outbox for ledger publication (Rulings 008.10/11/12/17)."""
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation

from . import state as state_mod

STATE_KEYS_DEC = ("E", "qty", "entry", "stop", "tp", "fees_total")


class StateCorruption(Exception):
    """Integrity Halt B: state corruption / non-atomic persistence failure."""


def _reject_dupes(pairs):
    keys = [k for k, _ in pairs]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise StateCorruption(f"duplicate JSON keys in state: {sorted(dupes)}")
    return dict(pairs)


def _enc_lc(lc):
    out = dict(lc)
    inv = dict(lc["invalidation"])
    inv["level"] = str(inv["level"])
    out["invalidation"] = inv
    return out


def _enc(acct):
    out = dict(acct)
    out.pop("_mark", None)
    out.pop("lifecycle", None)          # rebuilt from active_lifecycle_id
    for k in STATE_KEYS_DEC:
        out[k] = str(acct[k]) if acct[k] is not None else None
    out["lifecycles"] = [_enc_lc(lc) for lc in acct.get("lifecycles", [])]
    if acct.get("watch"):
        wc = dict(acct["watch"])
        wc["level"] = str(wc["level"])
        out["watch"] = wc
    return out


def _dec(acct):
    for k in STATE_KEYS_DEC:
        acct[k] = Decimal(acct[k]) if acct[k] is not None else None
    for lc in acct.get("lifecycles", []):
        lc["invalidation"]["level"] = Decimal(lc["invalidation"]["level"])
    acct["lifecycle"] = None
    if acct.get("active_lifecycle_id"):
        for lc in acct["lifecycles"]:
            if lc["lifecycle_id"] == acct["active_lifecycle_id"]:
                acct["lifecycle"] = lc
    if acct.get("watch"):
        acct["watch"]["level"] = Decimal(acct["watch"]["level"])
    return acct


def _enc_all(accounts):
    return {k: _enc(a) for k, a in accounts.items()}


def _checksum(enc_accounts, meta_core):
    payload = json.dumps({"accounts": enc_accounts, "meta": meta_core},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def save_state(path, accounts, meta):
    enc = _enc_all(accounts)
    meta_core = {k: v for k, v in meta.items() if k != "_checksum"}
    meta_out = dict(meta_core, _checksum=_checksum(enc, meta_core))
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"meta": meta_out, "accounts": enc}, f, indent=1, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _validate_account(aid, a):
    coin, model, arm = a.get("coin"), a.get("model"), a.get("arm")
    try:
        expected = state_mod.account_id(coin, model, arm)
    except (KeyError, TypeError, AttributeError):
        raise StateCorruption(f"invalid account identity fields: {aid}")
    if aid != expected:
        raise StateCorruption(f"account identity mismatch: {aid}")
    for k in STATE_KEYS_DEC:
        v = a.get(k)
        if v is None:
            continue
        try:
            d = Decimal(v)
        except (InvalidOperation, TypeError):
            raise StateCorruption(f"{aid}.{k} not Decimal-parseable: {v!r}")
        if not d.is_finite():
            raise StateCorruption(f"{aid}.{k} not finite")
    if Decimal(a["E"]) < 0:
        raise StateCorruption(f"{aid}.E negative")
    q = Decimal(a["qty"])
    if coin not in state_mod.QTY_DECIMALS:
        raise StateCorruption(f"unknown coin in account {aid}")
    quant = Decimal(1).scaleb(-state_mod.QTY_DECIMALS[coin])
    if q != q.quantize(quant):
        raise StateCorruption(f"{aid}.qty violates precision")
    if q == 0 and any(a.get(k) is not None for k in ("entry", "stop", "tp")):
        raise StateCorruption(f"{aid}: flat but entry/stop/tp set")
    if q != 0 and a.get("entry") is None:
        raise StateCorruption(f"{aid}: open position without entry")


def load_state(path, expect_full_roster=False, _allow_unchecksummed=False):
    """Strict production loader (Ruling 009.2): a valid checksum is REQUIRED.
    `_allow_unchecksummed` exists solely for the isolated migration helper
    `load_state_unverified` and must never be used on a running experiment."""
    try:
        with open(path) as f:
            payload = json.load(f, object_pairs_hook=_reject_dupes)
    except StateCorruption:
        raise
    except Exception as e:
        raise StateCorruption(f"unreadable state: {e}")
    meta = payload.get("meta", {})
    enc = payload.get("accounts", {})
    stored = meta.get("_checksum")
    meta_core = {k: v for k, v in meta.items() if k != "_checksum"}
    if stored is None:
        if not _allow_unchecksummed:
            raise StateCorruption("state checksum missing")
    elif stored != _checksum(enc, meta_core):
        raise StateCorruption("state checksum mismatch")
    try:
        for aid, a in enc.items():
            _validate_account(aid, a)
    except StateCorruption:
        raise
    except (KeyError, TypeError, AttributeError) as e:
        raise StateCorruption(f"malformed account record: {e!r}")
    if expect_full_roster:
        want = {state_mod.account_id(c, m, ar) for c in state_mod.COINS
                for m in state_mod.MODELS for ar in state_mod.ARMS}
        if set(enc) != want:
            raise StateCorruption("missing or extra accounts in state")
    accounts = {k: _dec(dict(a)) for k, a in enc.items()}
    return accounts, meta


def load_state_unverified(path):
    """MIGRATION/INITIALIZATION TOOLING ONLY — never used by the coordinator
    or any running experiment. Skips the checksum requirement (nothing else)."""
    return load_state(path, _allow_unchecksummed=True)


# ---------------- durable attempt archive (Ruling 008.11) ----------------

def attempt_path(store, rec):
    d = os.path.join(store, "attempts", rec["round_id"].replace(":", "_"))
    return os.path.join(d, f"{rec['account_id']}_attempt{rec['attempt_number']}.json")


def write_attempt(store, rec, validator=None):
    """Atomic per-record durability: temp + fsync + rename; duplicate-safe."""
    if validator is not None:
        validator(rec)
    path = attempt_path(store, rec)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return path                      # stable id => duplicate detected, keep first
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=1, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


# ---------------- durable prompt archive (Ruling 008.3) ----------------

def write_prompt_archive(store, round_seed, entries):
    """entries: list of dicts {account_id, pair_id, round_id, system, user,
    prompt_hash} plus {account_id, not_called: reason} markers. All files are
    fsynced before returning; any failure propagates (=> zero model calls)."""
    d = os.path.join(store, "prompts", round_seed)
    os.makedirs(d, exist_ok=True)
    for e in entries:
        path = os.path.join(d, f"{e['account_id']}.json")
        if os.path.exists(path):
            continue
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(e, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    return d


def read_prompt_archive(store, round_seed):
    d = os.path.join(store, "prompts", round_seed)
    out = {}
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        if fn.endswith(".json"):
            out[fn[:-5]] = json.load(open(os.path.join(d, fn)))
    return out


# ---------------- transactional outbox (Rulings 008.10/12) ----------------

def outbox_add(meta, event_id, payload):
    meta.setdefault("outbox", [])
    if any(e["id"] == event_id for e in meta["outbox"]) \
            or event_id in meta.get("flushed_ids", []):
        return False                     # duplicate publication detected
    meta["outbox"].append({"id": event_id, "payload": payload})
    return True


def flush_outbox(store, accounts, meta, crash=None):
    """Idempotently publish pending outbox events to ledger.jsonl, then mark
    them flushed atomically in the checkpoint. Restart re-runs this safely."""
    lpath = os.path.join(store, "ledger.jsonl")
    spath = os.path.join(store, "state.json")
    pending = list(meta.get("outbox", []))
    if not pending:
        return []
    published = read_ledger_ids(lpath)
    with open(lpath, "a") as f:
        for e in pending:
            if e["id"] in published:
                continue                 # duplicate detected by stable event ID
            f.write(json.dumps(dict(e["payload"], _event_id=e["id"]),
                               default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    if crash:
        crash("between_publish_and_mark")
    meta.setdefault("flushed_ids", [])
    meta["flushed_ids"] += [e["id"] for e in pending]
    meta["flushed_ids"] = meta["flushed_ids"][-2000:]
    meta["outbox"] = []
    save_state(spath, accounts, meta)
    return [e["id"] for e in pending]


def read_ledger_ids(path):
    return {e.get("_event_id") for e in read_ledger(path)}


def append_ledger(path, entries):
    with open(path, "a") as f:
        for e in entries:
            f.write(json.dumps(e, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_ledger(path):
    if not os.path.exists(path):
        return []
    out, seen = [], set()
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        eid = e.get("_event_id")
        if eid is not None and eid in seen:
            continue                     # dedupe by stable event id
        seen.add(eid)
        out.append(e)
    return out


def committed_round_ids(ledger):
    return {e["round_id"] for e in ledger
            if e.get("status") in ("PAIR_COMMITTED", "PAIR_ABORTED",
                                   "PAIR_TERMINAL_SPLIT")}


def boundary_already_processed(ledger, coin, round_id):
    return any(e.get("round_id") == round_id for e in ledger)


def write_heartbeat(path, t, code_hash, last_boundary):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"ts": t, "code_hash": code_hash,
                   "last_boundary_processed": last_boundary}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
