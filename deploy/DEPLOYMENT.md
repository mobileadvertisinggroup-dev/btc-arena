# VPS DEPLOYMENT RUNBOOK (Mentor Rulings 6 + 7)

Nothing here may be executed until the mentor's formal PASS and the owner's
deployment approval. GitHub is the source of truth: the VPS runs the audited
tree deployed from the approved branch — never manually edited on the server.

## 1. Server basics (one-time, owner or owner-approved)
- Ubuntu LTS VPS (~$5/mo class), user `arena` (no sudo), nginx, python3,
  python3-pip; `pip install --user dulwich jsonschema`.
- Clone the approved branch to /home/arena/btc-arena-v1 (deploy key or
  owner-scoped read token; never store the token on disk beyond git config
  scope the owner approves).
- Verify digests on the server before anything else:
  `python3 -c "from engine import config; print(config.build_manifest()['combined']); print(config.build_site_manifest()['combined'])"`
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

## 4. Service (Ruling 6)
- Install deploy/arena-official.service; systemctl enable --now.
- The service comes up ARMED/OFF: health.json shows {"state":"ARMED_OFF"}.
  It makes zero model calls and never touches data-v1 in this state.
- One-runner-only: control/runner.lock (flock). A second instance exits.

## 5. Server preflight (owner/mentor authorized; real model calls)
- Run tools/preflight_18.py against a SCRATCH store on the server (never
  data-v1), with the env file loaded. 18/18 must pass, publishing must be
  verified from the public URL, before activation is even proposed.

## 6. Activation (owner only)
- python3 scripts/arm_official.py --confirm \
    --engine-digest <externally issued> --site-digest <externally issued> \
    --start-utc next-hour
- The ARMED service validates the record + digests and starts the sealed
  336-boundary UTC schedule. Delete control/official_activation.json to
  disarm BEFORE start; stop the service to halt a live run (restart-safe:
  it resumes the same schedule; missed boundaries abort honestly).

## 7. Monitoring
- https://<vps-host>/health.json — state, pid, done/total, latest scheduled/
  terminal boundary, latest publication + status.
- journalctl -u arena-official -f for the service log (never contains
  secrets; the mirror token is read from env only).
- Daily sealed snapshots appear in evidence-official/daily/.

## 8. After boundary 336
- python3 scripts/archive_official.py --confirm  → final private archive +
  SHA-256 + sanitized FINAL_REPORT.md for the mentor and the public site.
