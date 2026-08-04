"""THE OWNER'S EXPLICIT ACTIVATION COMMAND (Mentor Ruling 6).

Writes control/official_activation.json — the record the ARMED/OFF service
polls for. Refuses unless ALL of:
  * --confirm literal flag;
  * --engine-digest / --site-digest: the two EXTERNALLY issued 64-hex
    mentor-approved digests (the tree cannot approve itself — this script
    also verifies the CURRENT tree matches them before writing anything);
  * --start-utc: a FUTURE exact UTC hour (epoch seconds divisible by 3600),
    or the literal "next-hour".

This script makes no network requests and no model calls; it only records
the owner's decision. Removing control/official_activation.json returns the
service to ARMED/OFF (it will not interrupt an already-running boundary
loop; stop the service to halt a live run).
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ACTIVATION = os.path.join(ROOT, "control", "official_activation.json")


def arg(name):
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def refuse(msg):
    print(f"REFUSED: {msg}\nNothing was written; the service stays ARMED/OFF.")
    sys.exit(2)


def main():
    if "--confirm" not in sys.argv:
        refuse("missing --confirm (owner-only explicit activation)")
    engine_digest = (arg("--engine-digest") or "").strip().lower()
    site_digest = (arg("--site-digest") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", engine_digest):
        refuse("--engine-digest must be the externally issued 64-hex "
               "mentor-approved engine digest")
    if not re.fullmatch(r"[0-9a-f]{64}", site_digest):
        refuse("--site-digest must be the externally issued 64-hex "
               "mentor-approved site digest")
    raw_start = arg("--start-utc") or ""
    now = int(time.time())
    if raw_start == "next-hour":
        start = (now // 3600 + 1) * 3600
    else:
        try:
            start = int(raw_start)
        except ValueError:
            refuse("--start-utc must be epoch seconds or 'next-hour'")
        if start % 3600:
            refuse(f"--start-utc {start} is not an exact UTC hour")
        if start <= now:
            refuse(f"--start-utc {start} is not in the future")
    from engine import config
    config.check_approved_digest(engine_digest)      # Halt A before any write
    config.check_approved_site_digest(site_digest)
    record = {"approved": "YES-OFFICIAL-RUN-APPROVED",
              "engine_digest": engine_digest, "site_digest": site_digest,
              "start_utc": start, "total": 336,
              "armed_at": now}
    os.makedirs(os.path.dirname(ACTIVATION), exist_ok=True)
    tmp = ACTIVATION + ".tmp"
    with open(tmp, "w") as f:
        json.dump(record, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ACTIVATION)
    print(f"ARMED: official 14-day run authorized. start_utc={start} "
          f"({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(start))}), "
          "336 boundaries.\nThe running service will pick this up within "
          "30 seconds. Delete the file to disarm before start.")


if __name__ == "__main__":
    main()
