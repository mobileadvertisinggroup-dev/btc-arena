"""Archive the 12-hour pilot and prepare FRESH official accounts (Rule 5).

Guarded: requires --confirm AND env ARENA_APPROVED_MANIFEST_SHA256 set to the
mentor-approved combined-manifest digest — the official store is provisioned
via engine.config.provision_store, never by self-approving the current tree
(Ruling 010.1). Never reuses pilot balances/trades/positions/reasoning.
Does NOT start the official experiment.

  ARENA_APPROVED_MANIFEST_SHA256=<digest> \
    python3 scripts/archive_pilot_reset.py --confirm
"""
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PILOT_STORE = os.path.join(ROOT, "data-pilot-12h")
OFFICIAL_STORE = os.path.join(ROOT, "data-v1")


def main():
    if "--confirm" not in sys.argv:
        print("REFUSED: pass --confirm to archive the pilot and create fresh "
              "official accounts. Nothing was changed.")
        sys.exit(2)
    digest = os.environ.get("ARENA_APPROVED_MANIFEST_SHA256", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        print("REFUSED: ARENA_APPROVED_MANIFEST_SHA256 not set to the "
              "mentor-approved 64-hex combined-manifest digest. The tree "
              "cannot approve itself. Nothing was changed.")
        sys.exit(2)
    from engine import config, state, persistence
    # Integrity gate BEFORE any archive/state work: current tree must match
    # the externally approved digest exactly, or nothing happens.
    config.check_approved_digest(digest)
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    # 1. preserve pilot results in a separate archive (page + data)
    arch = os.path.join(ROOT, "docs", "pilot-12h-archive")
    os.makedirs(arch, exist_ok=True)
    if os.path.isdir(PILOT_STORE):
        shutil.copytree(PILOT_STORE, os.path.join(arch, "data"),
                        dirs_exist_ok=True)
    for src, dst in (("live_payload.js", "final_payload.js"),
                     ("demo_payload.js", "demo_payload.js")):
        p = os.path.join(ROOT, "docs", src)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(arch, dst))
    with open(os.path.join(arch, "index.html"), "w") as f:
        f.write('<!doctype html><meta charset="utf-8">'
                '<title>12h Pilot Archive</title>'
                '<div style="background:#d03b3b;color:#fff;font-weight:700;'
                'padding:12px;text-align:center">ARCHIVED 12-HOUR PILOT — '
                'PAPER MONEY — NOT OFFICIAL EXPERIMENTAL EVIDENCE</div>'
                f'<p>Archived {stamp}. Raw data in <code>data/</code>; final '
                'dashboard payload in <code>final_payload.js</code>.</p>'
                '<p><a href="../">Back to AKRA ARENA</a></p>')
    # 2. eighteen completely fresh official accounts at exactly $10,000,
    #    in a store provisioned ONLY against the externally approved digest
    config.provision_store(OFFICIAL_STORE, digest)
    fresh = state.init_accounts()
    assert all(str(a["E"]) == "10000.00" and a["qty"] == 0 and not a["trades"]
               and not a["theses"] for a in fresh.values())
    persistence.save_state(os.path.join(OFFICIAL_STORE, "state.json"), fresh,
                           {"boundary": None, "official": True,
                            "created": stamp})
    print(f"pilot archived -> {arch}")
    print(f"18 fresh official accounts written -> {OFFICIAL_STORE}/state.json")
    print("Official 14-day experiment NOT started (separate authorization).")


if __name__ == "__main__":
    main()
