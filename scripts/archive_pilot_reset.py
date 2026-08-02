"""Archive the 24-hour pilot and prepare FRESH official accounts (Rule 5).

Guarded: requires --confirm. Never reuses pilot balances/trades/positions/
reasoning. Does NOT start the official experiment.

  python3 scripts/archive_pilot_reset.py --confirm
"""
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PILOT_STORE = os.path.join(ROOT, "data-pilot-24h")
OFFICIAL_STORE = os.path.join(ROOT, "data-v1")


def main():
    if "--confirm" not in sys.argv:
        print("REFUSED: pass --confirm to archive the pilot and create fresh "
              "official accounts. Nothing was changed.")
        sys.exit(2)
    from engine import state, persistence
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    # 1. preserve pilot results in a separate archive (page + data)
    arch = os.path.join(ROOT, "docs", "pilot-24h-archive")
    os.makedirs(arch, exist_ok=True)
    if os.path.isdir(PILOT_STORE):
        shutil.copytree(PILOT_STORE, os.path.join(arch, "data"),
                        dirs_exist_ok=True)
    payload_src = os.path.join(ROOT, "docs", "demo_payload.js")
    if os.path.exists(payload_src):
        shutil.copy(payload_src, os.path.join(arch, "final_payload.js"))
    with open(os.path.join(arch, "index.html"), "w") as f:
        f.write('<!doctype html><meta charset="utf-8">'
                '<title>24h Pilot Archive</title>'
                '<div style="background:#d03b3b;color:#fff;font-weight:700;'
                'padding:12px;text-align:center">ARCHIVED 24-HOUR PILOT — '
                'PAPER MONEY — NOT OFFICIAL EXPERIMENTAL EVIDENCE</div>'
                f'<p>Archived {stamp}. Raw data in <code>data/</code>; final '
                'dashboard payload in <code>final_payload.js</code>.</p>'
                '<p><a href="../">Back to BTC Arena</a></p>')
    # 2. eighteen completely fresh official accounts at exactly $10,000
    os.makedirs(OFFICIAL_STORE, exist_ok=True)
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
