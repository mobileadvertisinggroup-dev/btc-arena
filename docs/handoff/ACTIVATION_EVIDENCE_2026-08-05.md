# ACTIVATION EVIDENCE — AKRA ARENA OFFICIAL 14-DAY RUN (2026-08-05)

Final activation authorized by the owner + Mentor Ruling 025 (which
independently verified and accepted the server preflight). Executed
2026-08-05 ~11:03 UTC; sealed start T0 = 1785931200
(2026-08-05 12:00:00 UTC / 15:00 Beirut), 336 hourly boundaries, final
boundary 1787137200 (2026-08-19 11:00 UTC).

## Pre-arm verification battery (ALL passed before arming)
1. Server tree digests == the externally issued approvals
   (ENGINE 4336a0f8… / SITE 77339585…).
2. Preflight report bytes hash EXACTLY to the accepted SHA 54d69dc9….
3. Attestation fresh (age 955s at check) and T0 falls before its 24h
   expiry; T0 was in the future at arming.
4. scripts/verify_deployment.py with both digests: DEPLOYMENT
   VERIFICATION: PASS (zero failures) incl. NTP-synchronized clock.
5. Public health confirmed ARMED_OFF immediately before arming.

## The activation command (run on the VPS as arena, audited script)
  venv/bin/python scripts/arm_official.py --confirm
    --engine-digest 4336a0f8…d5b68e8 --site-digest 77339585…2ac4ed
    --start-utc 1785931200
    --preflight-report /home/arena/preflight-scratch/preflight_report_1785924000.json
    --preflight-sha 54d69dc9…f4b4
Output: "ARMED: official 14-day run authorized. start_utc=1785931200
(2026-08-05 12:00 UTC), 336 boundaries."

## Service pickup (with one designed self-recovery)
The long-running service predated the key installation, so its first pass
hit the intended CREDENTIAL HALT (exit 2); systemd Restart=on-failure
brought it back with /etc/arena/arena.env loaded and it accepted:
  "ACTIVATION ACCEPTED sha256=622e3447… start=1785931200 total=336"
  "OFFICIAL RUN ARMED — 336 boundaries from T0=1785931200 (UTC)"
The recoverable-provisioning transaction then created the official store.

## Post-arm verification (ALL passed)
A. control/official_activation.json contains EXACTLY the approved engine
   digest, site digest, start_utc 1785931200, total 336, and the preflight
   report path + SHA.
B. Official store data-v1/: exactly 18 pristine accounts, each
   E=$10,000.00, qty 0, zero trades, zero fees.
C. Schedule: exactly 336 UTC hourly boundaries, T0 1785931200 → final
   1787137200, completed=[].
D. verify_durable_trust(): binding + archived accepted activation +
   archived preflight attestation all validate.
E. READY publicly durable BEFORE the first boundary: the direct endpoint
   serves publication_id "1785931200:READY:0" (mode OFFICIAL_14D, progress
   0/336, official banner) with matching live_payload.sha256 sidecar
   995b0339…; publications.json records ready=PUBLISHED.
F. Public health: {"state": "WAITING", "boundaries_done": 0, "total": 336,
   "latest_scheduled": 1785931200,
   "latest_publication": {"publication_id": "1785931200:READY:0",
   "status": "PUBLISHED"}}.
G. Public dashboard honestly transitioned from PREPARATION to READY
   (screenshot): banner "OFFICIAL 14-DAY EXPERIMENT — REAL AI DECISIONS —
   PAPER MONEY", MODE OFFICIAL 14-DAY EXPERIMENT, ROUND STATUS READY,
   PROGRESS 0 / 336, ACCOUNTS live paper accounts, footer integrity
   4336a0f8… — reading the Hostinger endpoint (LIVE_ORIGIN).

## Unchanged (verified)
No approved source, prompt, configuration, model assignment, site file,
digest, endpoint, account balance, or experiment rule was changed. Local
pilot-era data-v1 untouched (fccdeae2…).

## Owner controls from here
- BEFORE 12:00 UTC: deleting control/official_activation.json on the VPS
  disarms with zero model calls (schedule rolls back; ARMED/OFF).
- AFTER the first boundary starts: systemctl stop arena-official halts;
  restart resumes the SAME sealed schedule; missed boundaries abort
  honestly; the schedule can never be replaced.
- Monitoring: https://live.akraarena.online/health.json and the public
  dashboard; daily sealed snapshots land in evidence-official/daily/;
  after boundary 336 the service exits cleanly and stays down
  (scripts/archive_official.py --confirm produces the final archive).
