# NEXT STEPS (in order; nothing here is authorized to ACTIVATE anything)

## 1. Build batch: cloud runner + UI wishlist (ONE audit round)
Owner requirements already standing:
- OFFICIAL 14-DAY RUNNER on an always-on cloud VPS (~$5/mo class) under
  systemd Restart=always. The official experiment must not depend on the
  owner's Mac being open/powered/connected or on Claude Code running.
  Needs a NEW runner entry (run_pilot_12h.py is pilot-specific: 12
  boundaries/pilot store/pilot banner) => engine digest WILL change =>
  full audit round required anyway.
- UI_WISHLIST.md (repo root): 12 recorded owner requirements — mobile fit
  (no horizontal wiggle), leaderboard+about off the landing page
  (click-to-open), "Feature"->"TA" labels, expandable thought process on
  open positions, working position age, render actual invalidation
  condition, Model Chat unchanged, remove demo button, obvious active coin
  tab, Raw-vs-TA comparison table, much smaller ticker, cloud move.
  (Age needs a payload addition in dashboard.py -> engine digest change,
  bundled anyway.)

## 2. Open design questions to put to the mentor BEFORE building
- Publication latency vs the T+12 hard deadline: the pilot lost the 21:00
  round to GitHub Pages build latency inside the THINKING gate. Options to
  propose: (a) accept occasional honest aborts; (b) budget publication
  separately (e.g. THINKING verification deadline T+Xmin, trading budget
  measured from verification success but capped at T+12 — needs ruling);
  (c) faster publication path (e.g. direct object hosting) — needs ruling.
- 14-day parameters to freeze: 336 hourly boundaries? maintenance windows?
  restart policy on VPS? evidence cadence (daily archives?); who holds the
  server credentials; monitoring/alerting responsibility.
- Whether the pilot archive page should be publicly linked (token scope for
  archiving was granted; deployment of the archive is not yet done).

## 3. Audit round
Per AUDIT_AND_DIGESTS.md: implement -> tests -> evidence -> archive ->
fresh-extraction verify -> report SHA + NEW engine/site digests -> mentor
formal PASS -> owner receives externally issued digests.

## 4. Server provisioning (owner action)
Owner provides VPS + SSH access (or approves GitHub Actions despite cron
jitter — jitter eats the T+12 budget, VPS recommended). Deploy, run the
18-request preflight ON THE SERVER, verify public publishing from there.

## 5. Official activation (owner-gated)
Only after: mentor PASS + new digests + server preflight PASS + explicit
owner activation command. 14-day experiment starts; Mac fully out of the
loop.

## FORBIDDEN without explicit new owner authorization
- Activating anything (preflight with real calls, scheduler, official run).
- Pushing/deploying to the public repo/site.
- Touching data-v1/ (pristine official accounts).
- Modifying the pilot archive or evidence.
- Any credential use beyond an owner-stated scope.
