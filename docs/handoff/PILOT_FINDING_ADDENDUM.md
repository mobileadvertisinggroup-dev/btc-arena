# POST-PILOT FINDING ADDENDUM — TEMPORAL ORDERING DEFECT (Mentor Ruling 016.1)

Recorded 2026-08-04. This addendum documents a defect discovered by the
mentor's independent review AFTER the 12-hour pilot was archived. The
immutable pilot archive (docs/pilot-12h-archive/, sealed tarball SHA-256
8d80b7cf7b9002aa03b847d8fdc47728c8e5794c8de3f93765e2292662dd823d) is NOT
altered by this addendum.

## The defect

The pilot-era boundary flow replayed the prior hour's 1-minute candles
[T-3600, T) AFTER rendering prompts, collecting model decisions, and
committing them at T. Consequences within the pilot:

- the T prompts could describe STALE account state (e.g. still long although
  the prior hour's candles had already crossed the position's stop);
- decisions were collected from that stale state;
- pre-T candles were then replayed after the T commit, so an exit belonging
  to the prior hour could be applied after — and interact with — an action
  taken at T.

## Status of the pilot

The pilot was already formally classified PAPER MONEY / SYSTEM TEST /
NOT VALID EXPERIMENTAL EVIDENCE (Mentor Final Ruling, 2026-08-03). This
defect adds a further, specific reason that classification is correct. No
pilot record is being changed; the ledger and archives faithfully record
what the pilot-era code actually did.

## The correction (official engine, Ruling 016.1)

The official coordinator now enforces the causal order per boundary:
fetch/validate data through T -> replay all 1m candles STRICTLY BEFORE T
against the pre-T state (phase persisted; crash-resume never replays a
candle twice) -> persist state/stops/latches/watermark -> freeze account
snapshots and P_T at T -> archive prompts -> call models -> commit at T.
A candle with timestamp < T can never touch an action taken at T. Regression
tests: tests/test_ruling016.py.
