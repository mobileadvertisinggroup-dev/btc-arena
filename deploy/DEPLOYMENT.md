# VPS DEPLOYMENT RUNBOOK (Mentor Rulings 6 + 7)

Nothing here may be executed until the mentor's formal PASS and the owner's
deployment approval. GitHub is the source of truth: the VPS runs the audited
tree deployed from the approved branch — never manually edited on the server.

## 1. Server basics (one-time, owner or owner-approved)
- Ubuntu LTS VPS (~$5/mo class), user `arena` (no sudo), nginx, python3,
  python3-venv.
- Clone the approved branch to /home/arena/btc-arena-v1 (deploy key or
  owner-scoped read token; never store the token on disk beyond git config
  scope the owner approves).
- Dedicated audited venv (Ruling 014.7 — the systemd unit executes ONLY
  this python):
    python3 -m venv /home/arena/btc-arena-v1/venv
    /home/arena/btc-arena-v1/venv/bin/pip install \
        -r /home/arena/btc-arena-v1/deploy/requirements-official.txt
- Verify digests on the server before anything else:
  `venv/bin/python -c "from engine import config; print(config.build_manifest()['combined']); print(config.build_site_manifest()['combined'])"`
  Both must equal the externally issued approved digests.

## 2. Secrets (Ruling 7)
- /etc/arena/arena.env  (root:arena, mode 0640), containing:
  ANTHROPIC_API_KEY=...            # owner-issued, official-run scope
  ARENA_PUBLIC_DIR=/var/www/arena
  ARENA_PUBLIC_PAYLOAD_URL=https://<vps-host>/live_payload.js
  ARENA_DEPLOY_TOKEN=...           # OPTIONAL, mirror-only scope
- Never committed, never in audit archives, never in payloads or logs.

## 3. Web endpoint (Ruling 1)
- mkdir -p /var/www/arena, owner arena:arena.
- Install deploy/nginx-arena.conf (replace ARENA_VPS_HOST), then TLS via
  certbot. Endpoint serves ONLY live_payload.js, live_payload.sha256,
  health.json; GET/HEAD only; CORS open; no-store.

## 4. Service (Rulings 6 + 014.3)
- Install deploy/arena-official.service; systemctl daemon-reload;
  systemctl enable --now arena-official.
- The service comes up ARMED/OFF: health.json shows {"state":"ARMED_OFF"}.
  It makes zero model calls and never touches data-v1 in this state.
- One-runner-only: control/runner.lock (flock). A second instance exits.
- Restart=on-failure: crashes and reboots recover automatically; after all
  336 boundaries the process exits cleanly (code 0) and stays down — no
  READY republication, no further publications, health stays COMPLETE
  (a reboot after completion re-exits cleanly the same way).

## 4b. Post-deployment verification (Ruling 014.6 — run after EVERY deploy)
- venv/bin/python scripts/verify_deployment.py --engine-digest <issued>
  Proves: tree matches the issued digest; installed systemd unit is
  byte-identical to the audited template; the effective nginx config still
  carries every audited directive (survives certbot rewrites); the audited
  venv python and exact pinned package versions are in use; the canonical
  endpoint constant is the frozen hostname. Any FAIL blocks activation.

## 5. Server preflight (owner/mentor authorized; real model calls)
- ARENA_PREFLIGHT_APPROVED=YES-OWNER-MENTOR-AUTHORIZED \
  ARENA_APPROVED_MANIFEST_SHA256=<issued> ARENA_APPROVED_SITE_SHA256=<issued> \
  venv/bin/python scripts/preflight_official.py /home/arena/preflight-scratch
  (audited script, hashed in the engine digest; SCRATCH store only — it
  refuses any path overlapping data-v1). 18/18 must pass AND the direct
  HTTPS endpoint probe must verify before activation is even proposed.

## 6. Activation (owner only)
- venv/bin/python scripts/arm_official.py --confirm \
    --engine-digest <externally issued> --site-digest <externally issued> \
    --start-utc next-hour
- The ARMED service validates the record + digests and starts the sealed
  336-boundary UTC schedule.
- REAL PRE-START DISARM (Ruling 014.1): deleting/replacing/modifying
  control/official_activation.json BEFORE the first boundary returns the
  service to ARMED/OFF with zero model calls (the exact record SHA is
  revalidated while waiting; the unstarted schedule is rolled back). After
  the first boundary starts: systemctl stop arena-official halts the run
  (restart-safe: it resumes the same schedule; missed boundaries abort
  honestly).

## 7. Monitoring
- https://<vps-host>/health.json — state, pid, done/total, latest scheduled/
  terminal boundary, latest publication + status.
- journalctl -u arena-official -f for the service log (never contains
  secrets; the mirror token is read from env only).
- Daily sealed snapshots appear in evidence-official/daily/.

## 8. After boundary 336
- venv/bin/python scripts/archive_official.py --confirm  → final private
  archive + SHA-256 + sanitized FINAL_REPORT.md for the mentor and the
  public site. The completed service stays down on its own (Ruling 014.3).
