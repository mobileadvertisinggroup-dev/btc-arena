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
    # Infrastructure integrity lock (Mentor Ruling 014.6): the systemd unit,
    # the Nginx endpoint template (frozen hostname), and the runtime
    # dependency lock are part of the ENGINE digest. verify_deployment.py
    # (a scripts/*.py file) is hashed automatically by _code_files().
    "deploy/arena-official.service",
    "deploy/nginx-arena.conf",
    "deploy/requirements-official.txt",
]

# Static production UI, frozen by its own externally approved manifest
# (Ruling 011.1). The DYNAMIC docs/live_payload.js is deliberately excluded:
# it changes every publication and must never be integrity-locked.
SITE_FILES = [
    "docs/index.html",
    "docs/prestart_payload.js",
    "docs/demo_payload.js",
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


def build_site_manifest():
    hashes = {p: file_hash(p) for p in sorted(SITE_FILES)}
    combined = hashlib.sha256(
        "".join(f"{p}:{h}\n" for p, h in sorted(hashes.items())).encode()
    ).hexdigest()
    return {"files": hashes, "combined": combined}


def verify_site_integrity(manifest):
    current = build_site_manifest()
    if current["combined"] != manifest["combined"]:
        diffs = [p for p in manifest["files"]
                 if current["files"].get(p) != manifest["files"][p]]
        raise IntegrityError(f"site-file hash mismatch: {diffs}")
    return True


def load_config():
    return load_json("config/v1/experiment.json")


def load_schema():
    return load_json("schemas/v1/decision.schema.json")


LAUNCH_MANIFEST_NAME = "launch_manifest.json"
SITE_MANIFEST_NAME = "site_manifest.json"


def write_launch_manifest(store):
    """TEST/TOOLING PROVISIONING ONLY: freeze the CURRENT tree as the approved
    launch manifest for a store. PRODUCTION activation must NEVER use this —
    it would let whatever tree exists at activation time approve itself
    (Ruling 010.1). Production paths use provision_store() with an externally
    supplied mentor-approved digest. The coordinator only LOADS the manifest
    and verifies against it."""
    path = os.path.join(store, LAUNCH_MANIFEST_NAME)
    with open(path, "w") as f:
        json.dump(build_manifest(), f, indent=1)
    with open(os.path.join(store, SITE_MANIFEST_NAME), "w") as f:
        json.dump(build_site_manifest(), f, indent=1)
    return path


def check_approved_digest(approved_digest):
    """Ruling 010.1: production activation requires an EXTERNALLY supplied
    mentor-approved combined-manifest digest. The current tree's combined
    manifest must equal it exactly, checked BEFORE any state initialization,
    prompt rendering, network access, or model call. Returns the verified
    current manifest; mismatch/absence => Integrity Halt A."""
    current = build_manifest()
    if not isinstance(approved_digest, str) or not approved_digest.strip() \
            or current["combined"] != approved_digest.strip().lower():
        raise IntegrityError(
            "current tree does not match the externally approved "
            f"combined-manifest digest (current={current['combined']}, "
            f"approved={approved_digest!r})")
    return current


def check_approved_site_digest(approved_site_digest):
    """Ruling 011.1: the static production UI (SITE_FILES) is frozen by its
    own EXTERNALLY approved combined digest, checked like the engine digest."""
    current = build_site_manifest()
    if not isinstance(approved_site_digest, str) \
            or not approved_site_digest.strip() \
            or current["combined"] != approved_site_digest.strip().lower():
        raise IntegrityError(
            "static site files do not match the externally approved site "
            f"manifest digest (current={current['combined']}, "
            f"approved={approved_site_digest!r})")
    return current


def atomic_write_json(path, obj):
    """Ruling 020.2: every provisioning write is temp + fsync + atomic
    replace — a crash can never leave a torn file."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _crash(crash_at, point):
    if crash_at == point:
        raise RuntimeError(f"provisioning crash injection: {point}")


def provision_store(store, approved_digest, approved_site_digest,
                    crash_at=None):
    """The ONLY way production code installs a store's manifests: verify the
    current tree AND the static site against their external approved digests,
    and only then copy the verified manifests in (atomically). Nothing is
    written on any mismatch."""
    current = check_approved_digest(approved_digest)
    site = check_approved_site_digest(approved_site_digest)
    os.makedirs(store, exist_ok=True)
    atomic_write_json(os.path.join(store, LAUNCH_MANIFEST_NAME), current)
    _crash(crash_at, "after_launch_manifest")
    atomic_write_json(os.path.join(store, SITE_MANIFEST_NAME), site)
    _crash(crash_at, "after_site_manifest")
    return store


def load_site_manifest(store):
    """Load the immutable approved site manifest. Missing/unreadable => halt."""
    path = os.path.join(store, SITE_MANIFEST_NAME)
    try:
        with open(path) as f:
            m = json.load(f, object_pairs_hook=_reject_dupes)
    except Exception as e:
        raise IntegrityError(f"approved site manifest unavailable: {e}")
    if "files" not in m or "combined" not in m:
        raise IntegrityError("approved site manifest malformed")
    return m


def load_launch_manifest(store):
    """Load the immutable approved manifest. Missing/unreadable => halt."""
    path = os.path.join(store, LAUNCH_MANIFEST_NAME)
    try:
        with open(path) as f:
            m = json.load(f, object_pairs_hook=_reject_dupes)
    except Exception as e:
        raise IntegrityError(f"approved launch manifest unavailable: {e}")
    if "files" not in m or "combined" not in m:
        raise IntegrityError("approved launch manifest malformed")
    return m
