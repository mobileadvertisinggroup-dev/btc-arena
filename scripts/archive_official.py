"""FINAL OFFICIAL-RUN ARCHIVE (Mentor Ruling 8) — owner --confirm required.

Runs ONLY after every scheduled boundary is terminal. Produces, under
evidence-official/final/:
  * official-final.tar.gz — the complete data-v1 store (state, ledger, all
    prompts/attempts, publications, payloads, schedule, manifests) + the
    per-file SHA-256 manifest; PRIVATE — the complete prompt/response archive
    is never published.
  * FINAL_REPORT.md — sanitized public report skeleton: methodology pointer,
    schedule facts, boundary outcome counts, final leaderboard from persisted
    marks, archive SHA-256 + manifest count. No secrets, no server paths, no
    raw prompts/responses.
Nothing in the store is modified, deleted, or reset.
"""
import json
import os
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

STORE = os.path.join(ROOT, "data-v1")
OUT = os.path.join(ROOT, "evidence-official", "final")


def main():
    if "--confirm" not in sys.argv:
        print("REFUSED: missing --confirm. Nothing was written.")
        sys.exit(2)
    from engine import official, persistence, pilot, publisher, state
    sched = pilot.load_schedule(STORE)
    remaining = [T for T in sched["boundaries"] if T not in sched["completed"]]
    if remaining:
        print(f"REFUSED: {len(remaining)} boundaries not terminal "
              f"(next: {remaining[0]}). The experiment is not over.")
        sys.exit(2)
    tar_path, sha = official.snapshot_store(STORE, OUT, "official-final")
    man = json.load(open(os.path.join(
        OUT, "official-final.MANIFEST.sha256.json")))
    accounts, meta = persistence.load_state(
        os.path.join(STORE, "state.json"), expect_full_roster=True)
    ledger = persistence.read_ledger(os.path.join(STORE, "ledger.jsonl"))
    statuses = [e["status"] for e in ledger if e.get("status")]
    marks = meta.get("marks") or {}
    rows = []
    for a in accounts.values():
        mark = marks.get(a["coin"])
        eq = (str(state.equity_at(a, Decimal(mark)))
              if (a["qty"] and mark is not None)
              else (str(a["E"]) if not a["qty"] else "MARK N/A"))
        rows.append((a["id"], eq, len(a["trades"])))
    rows.sort(key=lambda r: (r[1] == "MARK N/A",
                             -float(r[1]) if r[1] != "MARK N/A" else 0))
    log = publisher.read_log(STORE)
    published = sum(1 for e in log.values() if e.get("status") == "PUBLISHED")
    report = os.path.join(OUT, "FINAL_REPORT.md")
    with open(report, "w") as f:
        f.write(
            "# OFFICIAL 14-DAY EXPERIMENT — FINAL REPORT (sanitized, public)\n\n"
            f"- Schedule: {sched['total']} hourly UTC boundaries from "
            f"T0={sched['start']}; all terminal.\n"
            f"- Pair outcomes: "
            + ", ".join(f"{s}={statuses.count(s)}" for s in
                        ("PAIR_COMMITTED", "PAIR_ABORTED",
                         "PAIR_TERMINAL_SPLIT")) + "\n"
            f"- Publications recorded PUBLISHED: {published}\n"
            f"- Final marks (frozen Kraken, marks_T={meta.get('marks_T')}): "
            + ", ".join(f"{c}={marks.get(c)}" for c in ("BTC", "ETH", "SOL"))
            + "\n- Open positions at the final boundary are PRESERVED and "
              "marked at the frozen final marks (no forced closing trades).\n\n"
            "## Final equity (marked at the final boundary)\n\n"
            + "\n".join(f"{i+1}. {aid}  {eq}  ({nt} closed trades)"
                        for i, (aid, eq, nt) in enumerate(rows))
            + "\n\n## Evidence archive (private)\n\n"
            f"- official-final.tar.gz SHA-256: {sha}\n"
            f"- Manifest entries: {man['count']}\n"
            "- The complete prompt/response archive and server internals "
            "remain private per Mentor Ruling 8.\n")
    print(f"ARCHIVED: {tar_path}\nSHA-256: {sha}\n"
          f"Manifest entries: {man['count']}\nReport: {report}")


if __name__ == "__main__":
    main()
