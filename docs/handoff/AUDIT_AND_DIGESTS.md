# AUDIT AND DIGESTS

## CURRENTLY APPROVED — Mentor Ruling 022 (2026-08-05), build-batch tree
- ENGINE: 4336a0f83eb23071d8b19b58e3ee7ee120741ff9b6402d35e48e54215d5b68e8
- SITE:   773395854dd0e1b99f3f8cd168fcda5dc5dfc976c60b397883d913c3ef2ac4ed
- Approved archive: offline_audit_bundle_v10.tar.gz SHA-256
  ff602c73614b7a0bba930a3aff47eecf2753fd70d7eb1521bde8163c438eb388
  (157 manifest entries; independently verified by the mentor; audit rounds
  v2–v10 = Mentor Rulings 014–021 remediations, all archives preserved)
- DEPLOYED to the VPS 2026-08-05 in ARMED/OFF mode with a full
  verify_deployment.py PASS — see DEPLOYMENT_EVIDENCE_2026-08-05.md.
- Still gated: server preflight (owner/mentor authorization + key), Pages
  UI publication (owner token), activation (owner command with attestation).

## HISTORICAL — pilot-era approval (superseded for the official run)

## The two-digest activation model
1. ENGINE combined manifest = SHA-256 over: 8 canonical files
   (config/v1/experiment.json, both schemas, 5 prompt files) + every .py in
   engine/ and scripts/. Built by engine.config.build_manifest().
2. SITE manifest = SHA-256 over docs/index.html, docs/prestart_payload.js,
   docs/demo_payload.js (live_payload.js deliberately excluded — dynamic).
   Built by engine.config.build_site_manifest().

The mentor audits an archive, then EXTERNALLY issues the two approved
digests. Production provisioning (config.provision_store via pilot.provision
or archive_pilot_reset) verifies the CURRENT tree against those external
values BEFORE writing anything — the tree can never approve itself. The
coordinator re-verifies the stored manifests before every boundary, and the
publisher re-verifies site integrity before every publication.

## Currently approved (Mentor Final Ruling + Ruling 013, 2026-08-03)
- ENGINE: c425200e1b840524bb444288d6725bc1f728f60d94d72ed725d0fc704ec5432c
  (FROZEN — unchanged through pilot, archive, and this handoff)
- SITE:   2bfff2cb28b970c09a76b5a9045c4ffee909d37ccd8f39ab69001a95c264b7fa
- Last approved audit archive: offline_audit_bundle_v1.tar.gz
  SHA-256 9fe34520d90c2e4cce3aa54ca7b063a2623a17c7eb5bddcc4fdfcbb916414f9a
  (122 manifest entries; independently verified by the mentor)

## Pilot evidence archive (2026-08-03)
- docs/pilot-12h-archive/ + sealed tarball
  ~/trading-research-machine/pilot-12h-archive-final.tar.gz
  SHA-256 8d80b7cf7b9002aa03b847d8fdc47728c8e5794c8de3f93765e2292662dd823d
  (432 manifest entries, 432/432 verified, secret-scan clean)

## How to produce the NEXT audit round (cloud runner + UI batch)
1. Implement changes; keep all 258+ tests green; add regression tests for
   every new behavior; run mock_season (must stay twice-identical).
2. Regenerate evidence/ (pytest logs, junit, coverage, ruff, season hashes,
   canonical hashes, diff, line counts).
3. Rebuild archive: sorted file list over engine/tests/scripts/prompts/
   schemas/config/docs(-live_payload,-pilot archives)/evidence(+manifests),
   MANIFEST.sha256.json, tar.gz, report SHA-256 + sizes + counts.
4. Verify from a FRESH extraction: all manifest hashes match, full suite
   passes inside the extraction, both digests reproduce.
5. Report to the mentor: archive SHA-256, size, manifest count, and the two
   NEW digests activation will require. Await formal PASS + externally
   issued digests before ANY activation.

## Invariants the auditor probes (keep them true)
- Mutating any hashed byte => IntegrityError BEFORE any prompt/model/state
  work, zero caller invocations, zero mutation.
- State corruption (checksum, roster, ids, balances) => controlled
  StateCorruption, never raw KeyError, zero calls.
- Publication failure can never re-execute trading; retries are
  publication-only; THINKING failure => zero model calls for that boundary.
- Deadline is anchored at scheduled T; nothing extends it; exactly T+720.0
  is rejected.
- Null market values never render as zero anywhere in the UI.
