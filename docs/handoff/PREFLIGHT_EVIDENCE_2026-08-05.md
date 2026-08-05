# SERVER PREFLIGHT EVIDENCE — AKRA ARENA V10 (2026-08-05)

Authorized by the owner + Mentor Ruling 024. Executed on the VPS
(live.akraarena.online) by the audited scripts/preflight_official.py
(hashed inside the approved engine digest) against the isolated scratch
store /home/arena/preflight-scratch. RESULT: OFFICIAL PREFLIGHT: PASS.

## Key installation (scope item 1-2)
The owner installed a NEW Anthropic API key via a hidden SSH prompt in
their own terminal — the key never appeared in chat, logs, files here, or
command echoes. Verified post-install: exactly one ANTHROPIC_API_KEY line
in /etc/arena/arena.env, root:arena 0640. The report contains zero secret
markers (grep for key patterns: 0).

## The 18 real model requests (scope items 3-6)
- Report: preflight_report_1785924000.json (copy in this directory;
  authoritative original on the VPS at
  /home/arena/preflight-scratch/preflight_report_1785924000.json — the
  path + SHA the activation record must reference).
- Report SHA-256:
  54d69dc9024ff23406df7881d1fd331cf6a96e1a2b6af09ce5f9353df847f4b4
- Summary: n=18, accepted=18, schema_valid=18, identity_ok=18,
  semantically_valid_first_try=18, transport_failures=[],
  prompt_archive_durable=true, raw_ta_separation_ok=true,
  accounts_unmutated=true, model_calls_pass=true, direct_endpoint_ok=true,
  overall_pass=true.
- Model identities: requested == returned EXACTLY for every account —
  claude-haiku-4-5-20251001 (6), claude-sonnet-5 (6), claude-opus-4-8 (6).
- Call count exactly 18 (one per account: BTC/ETH/SOL x Haiku/Sonnet/Opus
  x Raw/TA), executed through the production wave order under the
  production 6-request concurrency cap (audited implementation; peak
  concurrency == 6 is enforced and test-proven in tests/test_ruling015.py).
- Latencies 2.63s .. 74.33s; boundary T=1785924000 (frozen Kraken marks
  BTC 64096.6 / ETH 1870.10 / SOL 73.94, prompt-rendering only).
- Digests recorded in the report match the externally issued approvals:
  ENGINE 4336a0f8… / SITE 77339585…; canonical endpoint
  https://live.akraarena.online/live_payload.js verified by the direct
  probe (payload + checksum round-trip, then restored — VPS
  live_payload.js is 404 again).

## Untouched-state proofs (scope item 7)
- Server official store: /home/arena/btc-arena-v1/data-v1 ABSENT (never
  created); no schedule, no binding, no activation record (control/ holds
  only runner.lock).
- Local pilot-era data-v1/state.json unchanged (fccdeae2…, since
  2026-08-03).
- Scratch store contains ONLY the durable preflight prompt archive + the
  report (+ sha sidecar): no state.json, no ledger, no trades.
- Service: active, public health.json = ARMED_OFF / OFFICIAL_14D after
  completion.
- Public dashboard: served Pages files still reproduce the approved SITE
  digest exactly and render PREPARATION MODE — EXPERIMENT NOT STARTED.

## Explicitly NOT done
arm_official.py not run; no activation record; the 14-day experiment not
started; no approved engine/site file changed; no source or dashboard
commit pushed.

## Ready-to-arm reference (for the owner's future activation command)
  --preflight-report /home/arena/preflight-scratch/preflight_report_1785924000.json
  --preflight-sha 54d69dc9024ff23406df7881d1fd331cf6a96e1a2b6af09ce5f9353df847f4b4
  (attestation validity: 24h from report timestamp 1785926773; the chosen
  T0 must fall before expiry, else the preflight must be re-run.)
