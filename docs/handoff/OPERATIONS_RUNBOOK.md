# OPERATIONS RUNBOOK

All commands from repo root: ~/trading-research-machine/btc-arena-v1

## Everyday (safe, offline)
- Full hermetic suite:        python3 -m pytest tests/ -q        (258 tests)
- Lint gate:                  ~/Library/Python/3.13/bin/ruff check engine tests scripts --select F
- Deterministic mock season:  python3 scripts/mock_season.py     (writes evidence/)
- Digest check:               python3 -c "from engine import config; print(config.build_manifest()['combined']); print(config.build_site_manifest()['combined'])"
- Regenerate demo/prestart payloads (changes SITE digest — audit impact):
                              python3 scripts/gen_demo_dashboard.py

## Git (dulwich only — no git CLI on this machine)
- from dulwich import porcelain; porcelain.add(".", paths=[...]);
  porcelain.commit(".", message=..., author=..., committer=...)
- Push (only when owner-authorized):
  porcelain.push(".", "https://x-access-token:<TOKEN>@github.com/mobileadvertisinggroup-dev/btc-arena.git", "v1-clean-experiment")
- Pages serves v1-clean-experiment:/docs. Pushing the branch publishes the
  entire repo content publicly.

## Preflight (owner/mentor authorized only; real model calls)
  set -a && . ../btc-arena/.env && set +a
  python3 tools/preflight_18.py <scratch-store-dir>
Validation only: 18 real calls, zero execution/mutation. Verifies digests
first; writes preflight_report_<T>.json.

## Pilot activation (HISTORICAL — completed 2026-08-03; same pattern will be
## adapted for the official runner)
  ARENA_PILOT_APPROVED=YES-AUDIT-PASSED \
  ARENA_APPROVED_MANIFEST_SHA256=<approved engine digest> \
  ARENA_APPROVED_SITE_SHA256=<approved site digest> \
  ARENA_DEPLOY_TOKEN=<owner-scoped token> ANTHROPIC_API_KEY=<key> \
  caffeinate -i python3 -u scripts/run_pilot_12h.py --activate
Behavior: provision (idempotent, keeps persisted schedule) -> READY publish +
PUBLIC verification gate -> per boundary: THINKING gate (fail => halt with
zero calls; restart retries publication only) -> Kraken fetch -> coordinator
with ABSOLUTE deadline T+720s -> ROUND_COMMITTED publish. A crash/halt is
always safe: relaunch the same command; it resumes the same schedule and
aborts expired boundaries honestly (deadline_exceeded / crash_recovery).

## Archive/reset (owner --confirm required; ran 2026-08-03)
  ARENA_APPROVED_MANIFEST_SHA256=<engine> ARENA_APPROVED_SITE_SHA256=<site> \
  python3 scripts/archive_pilot_reset.py --confirm

## Monitoring pattern used during runs
- Log: ~/trading-research-machine/pilot-12h.log (contains push URLs — the
  raw log holds the token; the ARCHIVED copy is sanitized. Treat the live
  log as secret; never commit it.)
- Publication log: <store>/publications.json (statuses PUBLISHED/FAILED/
  SUPERSEDED per lifecycle key).
- Liveness: pgrep -f run_pilot; public URL check with ?cb=<anything>.

## Known operational lessons (from the pilot)
1. Home network outage at a boundary => THINKING gate halts trading (correct).
   Restart after connectivity returns; the missed boundary aborts.
2. GitHub Pages deploy latency counts against the T+12min budget: a slow
   Pages build can honestly abort a round (happened once). The cloud design
   must budget publication latency or the mentor must approve a faster
   publication path. See NEXT_STEPS.md open questions.
3. macOS: caffeinate -i does NOT survive lid close; only a cloud host truly
   removes machine dependence.
4. Token typos fail safely (READY gate refuses with zero calls) — but check
   env values character-by-character before launching.
