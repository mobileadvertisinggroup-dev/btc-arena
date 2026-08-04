# BUILD BATCH DESIGN — cloud runner + UI wishlist (one audit round)

Branch: build-batch-cloud-ui (off v1-clean-experiment @ d05a5c6).
Implements Mentor Rulings 1–9 under the Ruling 10 authorization. Nothing is
activated, deployed, pushed, or preflighted; data-v1/ is untouched.

## Architecture decisions (mapping rulings → code)

### R1 — publication decoupled from GitHub Pages
The official runner publishes by atomically writing `live_payload.js` +
`live_payload.sha256` into a local **public directory** served read-only by
Nginx on the VPS, then verifies the payload from the direct HTTPS endpoint:
exact `publication_id` AND exact SHA-256 of the payload text must match.
GitHub Pages becomes an **asynchronous mirror** (best-effort thread, never in
any deadline path, only if a deploy token is present). The dashboard page
gains a `LIVE_ORIGIN` constant: it polls `LIVE_ORIGIN + 'live_payload.js'`
(the VPS endpoint) and falls back to its own origin if unset/unreachable.
**OWNER INPUT REQUIRED before the audit archive is sealed: the final VPS
domain, so LIVE_ORIGIN can be frozen into the site digest.** Nginx config
serves GET/HEAD only with CORS + no-store headers; no endpoint accepts
commands, prompts, credentials, or state changes.

### R2 — explicit sub-budgets inside T+12:00
Constants in engine/official.py (hard values, audited):
- T+00:05 start market fetch; per-coin fetch failure or budget exhaustion at
  **T+00:30** => that coin is DATA_UNAVAILABLE (existing semantics); THINKING
  payload made durable locally by T+00:30.
- **T+01:00** — direct public THINKING verification deadline. Not verified =>
  the boundary aborts with ZERO model calls: the coordinator runs with
  `abort_all_reason="thinking_not_verified"`, producing 9 honest PAIR_ABORTED
  ledger records; marks are still frozen; the boundary becomes terminal.
- **T+08:30** — coordinator collection deadline (`deadline=T+510` passed to
  recovery.run_checkpointed): model calls + approved retry semantics.
- **T+10:30** — resolution/accounting finish (guaranteed by construction:
  resolution is local and follows collection immediately; asserted in tests).
- **T+11:30** — final payload durable + direct publication verified
  (publisher deadline T+690). Failure => persisted FAILED, publication-only
  retry later; the boundary stays terminal. Trading never re-executes.
- **T+12:00** — absolute hard stop (unchanged coordinator invariant).
Never trade late, never replay a missed boundary, never reuse a stale model
response (each boundary renders + archives fresh prompts; responses commit
only inside their own boundary's coordinator run — unchanged).

### R3 — official schedule
`provision_official()` = digest-gated pilot.provision with total=336 and a
start that must be an exact future UTC hour chosen by the owner at
activation. The persisted schedule (start, 336 boundaries, completed set) IS
the sealed record, written before any model call; restarts resume it and
never create new boundaries. All timestamps epoch/UTC; Beirut is
display-only. At boundary 336 the last coordinator run freezes final Kraken
marks and equity history exactly like every boundary; open positions are
preserved; no forced closes exist anywhere in the engine.

### R4 — experiment config
Untouched: coins, models, arms, 18 accounts, $10,000, Kraken, leverage,
execution, lifecycle, fees, deadline parameters, schemas, prompts.
"Feature"→"TA" is display text in docs/index.html only; ids/keys/paths keep
`ta`/`feature` internally.

### R5 — age + invalidation from existing state (publication layer only)
Position entry time already exists: the ACTIVE lifecycle's `start_t` (set at
open; reversal creates a new lifecycle; averaging keeps the original — the
correct entry boundary). dashboard.py exposes it as `entry_t` on each account
row; no trading-logic change. The UI computes age deterministically as
`published_boundary − entry_t` (never wall clock). Invalidation
operator/level/timeframe/status are already in the payload; the Open
Positions panel now renders the exact submitted condition, e.g.
"NOT TRIGGERED — invalidates if price ≥ 63,900 (1h close)"; nothing is
invented when no invalidation exists.

### R6 — process control
- systemd unit with Restart=on-failure (per Mentor Ruling 014.3: crash and
  reboot recovery without restarting a completed experiment)
  + WantedBy=multi-user.target (auto-start
  after reboot); state lives in the repo checkout + /var/www/arena (never
  tmpfs/container-ephemeral).
- One-runner-only: exclusive non-blocking fcntl flock on control/runner.lock;
  a second instance exits immediately.
- ARMED/OFF: the service loop idles (zero model calls, zero scheduling,
  data-v1 untouched) until the owner's explicit activation command
  (scripts/arm_official.py --confirm) writes control/official_activation.json
  with the externally issued digests + chosen start hour. Store separation:
  data-v1 (official) vs data-pilot-12h (archived pilot) vs scratch stores for
  preflight/tests.
- health.json in the public dir: state (ARMED_OFF/WAITING/BOUNDARY_ACTIVE/
  COMPLETE), pid, boundaries done/total, latest scheduled boundary, latest
  terminal boundary, latest publication id+status, updated timestamp.

### R7 — credentials & deployment
GitHub stays source of truth; VPS runs the audited tree deployed from the
approved branch (deploy/DEPLOYMENT.md documents the flow; no manual edits on
the server). Secrets only in /etc/arena/arena.env (root:arena, 0640),
referenced by systemd EnvironmentFile; never in repo/archive/payloads/logs.

### R8 — evidence & monitoring
Per boundary (all existing, verified retained): frozen snapshots/marks,
durable prompt archive, attempt records (raw responses), validation results,
lifecycle/accounting outbox → ledger.jsonl, publications.json (id, sha256,
status), terminal pair records. New: `snapshot_store()` writes a sealed
tar.gz + SHA-256 manifest of the store after every UTC day (daily snapshot)
into evidence-official/; scripts/archive_official.py builds the final
immutable archive + manifest + sanitized public report skeleton after
boundary 336. Full archive stays private; the public site shows methodology,
sanitized results, payload history, archive SHA-256 + manifest count.

### R9 — engine/site separation
UI work is entirely in docs/index.html (+ regenerated demo/prestart
payloads). Engine and site digests remain separate; payload-contract changes
in this round (entry_t, mode/banner fields) ship in the same combined audit.

## File-by-file plan

MODIFIED — engine (engine digest changes):
- engine/recovery.py — add optional `abort_all_reason` to run_checkpointed():
  when set, zero model calls; every non-finalized pair → PAIR_ABORTED with
  that reason; prompt archive gets not_called markers; marks still freeze.
  No behavior change when the parameter is absent (existing 258 tests prove).
- engine/publisher.py — optional `branding` (mode/banner) parameter threaded
  through build_live_payload/publish_ready/publish_thinking/publish_boundary/
  reconcile; defaults preserve exact pilot output byte-for-byte.
- engine/dashboard.py — `entry_t` per account row (active lifecycle start_t,
  null when flat).

NEW — engine:
- engine/official.py — budget constants; provision_official(); run_official()
  (fetch→THINKING→verify(T+60)→coordinator(T+510)→terminal→publish(T+690),
  recovery wired, health writer, daily snapshots, async mirror hook that can
  never gate or raise into trading); DirectPublisher (atomic payload+sha256
  write, injected fetchers, per-call deadline, checksum+id verification);
  acquire_runner_lock(); write_health(); snapshot_store().

NEW — scripts (engine digest):
- scripts/run_official_14d.py — service entry: lock → ARMED/OFF loop →
  validate activation record + env → digest-gated provision of data-v1 →
  run_official with real Kraken fetch, real Anthropic caller, DirectPublisher
  (ARENA_PUBLIC_DIR + ARENA_PUBLIC_PAYLOAD_URL), optional async Pages mirror.
- scripts/arm_official.py — the owner's explicit activation command
  (validates 64-hex digests, future full UTC hour, --confirm; atomic write).
- scripts/archive_official.py — final archive + manifest + sanitized report
  (refuses while boundaries remain).

NEW — deploy (not hashed; included in audit archive):
- deploy/arena-official.service, deploy/nginx-arena.conf,
  deploy/DEPLOYMENT.md.

MODIFIED — site (site digest changes):
- docs/index.html — wishlist #1–#11 (see UI_WISHLIST.md), official-mode
  banners/labels, LIVE_ORIGIN polling, age + invalidation rendering,
  Raw-vs-TA comparison table, demo button removed (?demo=1 preserved).
- docs/prestart_payload.js, docs/demo_payload.js — regenerated
  (gen_demo_dashboard.py) so demo/prestart rows carry the new fields.

TESTS:
- tests/test_official.py (new) — budgets incl. T+60 abort with zero calls
  and T+510 collection cutoff; sealed 336-boundary UTC schedule; ARMED/OFF
  gate; lock exclusivity; direct publisher checksum verification (tamper ⇒
  FAILED, trading unaffected); mirror failure isolation; health contents;
  daily snapshot; final-boundary position preservation; entry_t payload.
- tests/test_ruling011/012.py — display-label updates only (Feature→TA).
- tests/frontend_harness.js — unchanged mechanism; new DOM assertions live in
  python tests (official banner, age text, invalidation text, comparison
  table, no demo button, leaderboard hidden until opened).

DOCS: CURRENT_STATUS/NEXT_STEPS/OPERATIONS_RUNBOOK updated at the end of the
round; UI_WISHLIST.md annotated with implementation status.

## Out of scope (unchanged, still forbidden)
Model calls, any preflight/soak, server or Pages deployment, pushes,
data-v1/ writes, pilot archive/evidence edits.
