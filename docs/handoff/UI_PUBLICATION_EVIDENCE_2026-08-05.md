# UI PUBLICATION EVIDENCE — APPROVED V10 DASHBOARD TO GITHUB PAGES (2026-08-05)

Owner-authorized publication of the formally approved static dashboard
(Mentor Rulings 020/022 — SITE PASS). Secure browser-based GitHub device
authentication (no token pasted in chat).

## What was published
- Branch v1-clean-experiment fast-forwarded to the approved tree and pushed
  to mobileadvertisinggroup-dev/btc-arena; remote head efc852adb3e6.
- Pre-publication commit efc852ad removed the stale pilot
  docs/live_payload.js (a DYNAMIC file deliberately excluded from the site
  digest) so the public dashboard boots in PREPARATION mode; the pilot's
  final payload remains preserved in the sealed pilot archive. Both
  approved digests re-verified unchanged after this commit.

## Public verification (fetched over the public internet)
- Served docs/index.html + prestart_payload.js + demo_payload.js: combined
  SHA-256 of the SERVED bytes =
  773395854dd0e1b99f3f8cd168fcda5dc5dfc976c60b397883d913c3ef2ac4ed
  — EXACTLY the externally issued approved SITE digest.
- Served page contains: "PREPARATION MODE — EXPERIMENT NOT STARTED" banner;
  const LIVE_ORIGIN = 'https://live.akraarena.online/' (reads the Hostinger
  endpoint first, same-origin fallback); RAW vs TA language; no demo
  button.
- live_payload.js on Pages: 404 (correct — no live publication exists).
- Screenshot evidence: full-page dashboard in PREPARATION mode (all model
  cards WAITING, PROGRESS 0 / —, accounts inactive) and the VPS
  health.json showing ARMED_OFF / OFFICIAL_14D.

## Credential handling
- gh device-flow login by the owner in the browser
  (account mobileadvertisinggroup-dev; scopes gist, read:org, repo).
- INCIDENT + REMEDIATION: dulwich's push progress line echoed the push URL
  containing the gh OAuth token into the local session transcript. The
  token was never written to any file or committed. Remediation, completed
  immediately after verification: gh auth logout (local credential
  removed) AND the owner revoked the GitHub CLI OAuth grant in the browser,
  invalidating the token server-side. Future pushes will re-authenticate
  via the device flow per publication.

## Explicitly NOT done
No real-model preflight, no Anthropic calls, no activation record, no
14-day start, no engine or data-v1 modification. The engine remains
ARMED/OFF on the VPS.
