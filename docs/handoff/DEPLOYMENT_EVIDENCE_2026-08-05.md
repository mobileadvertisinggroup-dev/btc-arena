# DEPLOYMENT EVIDENCE — APPROVED V10 TREE TO VPS (2026-08-05)

Authorized by the owner following Mentor Ruling 022 (formal ENGINE + SITE
PASS). Deployed in ARMED/OFF — EXPERIMENT NOT STARTED mode.

## Externally issued approved digests (Ruling 022)
- ENGINE: 4336a0f83eb23071d8b19b58e3ee7ee120741ff9b6402d35e48e54215d5b68e8
- SITE:   773395854dd0e1b99f3f8cd168fcda5dc5dfc976c60b397883d913c3ef2ac4ed

## Deployment artifact and transport
- Artifact: offline_audit_bundle_v10.tar.gz — the mentor-verified sealed
  archive (SHA-256 ff602c73614b7a0bba930a3aff47eecf2753fd70d7eb1521bde8163c438eb388,
  157-entry manifest). No GitHub deploy token was issued in this
  authorization, so the verified bundle itself was transferred over SSH; its
  manifest + the externally issued digests are the integrity proof.
- Server-side verification after extraction to /home/arena/btc-arena-v1:
  - tarball SHA-256 re-verified on the VPS: OK
  - manifest: 157/157 entries verified, zero mismatches
  - server tree digests: ENGINE 4336a0f8… / SITE 77339585… — EXACT match to
    the externally issued approved values.

## Installed components
- Pinned venv (/home/arena/btc-arena-v1/venv): dulwich 1.2.12,
  jsonschema 4.26.0, attrs 26.1.0, referencing 0.37.0, rpds-py 2026.6.3,
  jsonschema-specifications 2025.9.1 (+ transitive typing_extensions,
  urllib3) — every lock pin exact.
- systemd: /etc/systemd/system/arena-official.service byte-identical to the
  audited template (sha256 f918bd1dff67…, matched), enabled for boot,
  Restart=on-failure, running under the audited venv python (PID recorded).
- Nginx: audited deploy/nginx-arena.conf installed verbatim; certbot
  re-applied the TLS wrap + HTTP->HTTPS redirect (standard two-block
  layout); nginx -t OK; reloaded.
- /etc/arena/arena.env (root:arena, 0640): ARENA_PUBLIC_DIR and the
  canonical ARENA_PUBLIC_PAYLOAD_URL only — NO secrets present (the
  Anthropic key is issued at activation).

## Service state
- arena-official: active (running), ARMED/OFF loop; public
  https://live.akraarena.online/health.json serves
  {"state": "ARMED_OFF", "mode": "OFFICIAL_14D", ...}.
- Zero model calls, zero scheduling, no activation record, data-v1 (local
  archive-era store) untouched; the server store is empty pending
  activation-time provisioning under the approved digests.

## Public endpoint checks (from the public internet)
- https health.json: 200, ARMED_OFF payload
- http -> https: 301 to https://live.akraarena.online/health.json
- / : 404 (nothing else exposed)
- POST health.json: 403 (GET/HEAD only)
- live_payload.js: 404 (no publication exists — correct pre-activation)

## scripts/verify_deployment.py (run on the VPS with BOTH issued digests)
Result: DEPLOYMENT VERIFICATION: PASS — every check passed:
engine digest match; site digest match; systemd unit byte-identical;
exactly one HTTPS content block + exactly one HTTP redirect block; no
proxy/fastcgi/uwsgi/scgi/grpc/dav/alias/autoindex/rewrite/upload surface;
no server-level returns or if-blocks in the HTTPS block; location / = 404
only; approved three-file payload location with GET-only + CORS + no-store
headers; live HTTPS 200 + HTTP 301 redirect; NTP synchronized
(NTPSynchronized=yes); audited venv python; all six pins exact; canonical
endpoint constant equals the frozen hostname.

## Outstanding item (owner action required)
"Publish the approved UI in preparation mode" targets the GitHub Pages
dashboard (mobileadvertisinggroup-dev/btc-arena, branch
v1-clean-experiment:/docs). Publishing requires pushing the approved docs/
files, which requires an owner-issued deploy token — none was provided with
this authorization, and credentials are only ever used within an explicitly
stated scope. Everything VPS-side is complete; the Pages site still shows
the archived pilot payload until the owner issues a token scoped to
"publish the approved v10 docs/ to the btc-arena repo".

## Explicitly NOT done (per authorization)
No real-model preflight, no Anthropic call, no activation record, no
experiment start, no data-v1 modification, no source or static-site change,
no digest substitution.
