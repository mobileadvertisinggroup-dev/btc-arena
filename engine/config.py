"""Configuration loading, canonical-file hashing, and integrity verification."""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CANONICAL_FILES = [
    "config/v1/experiment.json",
    "schemas/v1/decision.schema.json",
    "schemas/v1/records.schema.json",
    "prompts/v1/system.txt",
    "prompts/v1/user_raw.txt",
    "prompts/v1/user_feature.txt",
    "prompts/v1/blocks.md",
    "prompts/v1/placeholders.json",
]


def _code_files():
    """Every engine source + production scripts, deterministic sorted order."""
    out = []
    for top in ("engine", "scripts"):
        base = os.path.join(ROOT, top)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            out += [os.path.relpath(os.path.join(root, f), ROOT)
                    for f in files if f.endswith(".py")]
    return sorted(out)


class IntegrityError(Exception):
    """Raised on hash mismatch => INTEGRITY HALT A."""


def _reject_dupes(pairs):
    keys = [k for k, _ in pairs]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ValueError(f"duplicate JSON keys: {sorted(dupes)}")
    return dict(pairs)


def load_json(relpath):
    with open(os.path.join(ROOT, relpath)) as f:
        return json.load(f, object_pairs_hook=_reject_dupes)


def load_text(relpath):
    with open(os.path.join(ROOT, relpath)) as f:
        return f.read()


def file_hash(relpath):
    with open(os.path.join(ROOT, relpath), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build_manifest():
    hashes = {p: file_hash(p) for p in sorted(set(CANONICAL_FILES) | set(_code_files()))}
    combined = hashlib.sha256(
        "".join(f"{p}:{h}\n" for p, h in sorted(hashes.items())).encode()
    ).hexdigest()
    return {"files": hashes, "combined": combined}


def verify_integrity(manifest):
    current = build_manifest()
    if current["combined"] != manifest["combined"]:
        diffs = [p for p in manifest["files"]
                 if current["files"].get(p) != manifest["files"][p]]
        raise IntegrityError(f"canonical-file hash mismatch: {diffs}")
    return True


def load_config():
    return load_json("config/v1/experiment.json")


def load_schema():
    return load_json("schemas/v1/decision.schema.json")
